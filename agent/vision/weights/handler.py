import base64
import io
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import cv2
import easyocr
import numpy as np
import torch
from PIL import Image
from PIL.Image import Image as ImageType
from supervision.detection.core import Detections
from supervision.draw.color import Color, ColorPalette
from torchvision.ops import box_convert
from torchvision.transforms import ToPILImage
from transformers import AutoModelForCausalLM, AutoProcessor
from transformers.image_utils import load_image
from ultralytics import YOLO

# NOTE: here so that it's downloaded before hand so that the endpoint it not stuck listening, whilst the required
# files are still being downloaded
easyocr.Reader(["en"])


class EndpointHandler:
    def __init__(self, model_dir: str = "/repository") -> None:
        self.device = (
            torch.device("cuda") if torch.cuda.is_available()
            else (torch.device("mps") if torch.backends.mps.is_available()
                  else torch.device("cpu"))
        )

        # bounding box detection model
        self.yolo = YOLO(f"{model_dir}/icon_detect/model.pt")

        # captioning model
        self.processor = AutoProcessor.from_pretrained(
            "microsoft/Florence-2-base", trust_remote_code=True
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            f"{model_dir}/icon_caption",
            torch_dtype=torch.float16,
            trust_remote_code=True,
        ).to(self.device)

        # ocr
        self.ocr = easyocr.Reader(["en"])

        # box annotator
        self.annotator = BoxAnnotator()

    def __call__(self, data: Dict[str, Any]) -> Any:
        # data should contain the following:
        #  "inputs": {
        #    "image": url/base64,
        #    (optional) "image_size": {"w": int, "h": int},
        #    (optional) "bbox_threshold": float,
        #    (optional) "iou_threshold": float,
        #  }
        data = data.pop("inputs")

        # read image from either url or base64 encoding
        image = load_image(data["image"])

        ocr_texts, ocr_bboxes = self.check_ocr_bboxes(
            image,
            out_format="xyxy",
            ocr_kwargs={"text_threshold": 0.8},
        )
        annotated_image, filtered_bboxes_out = self.get_som_labeled_img(
            image,
            image_size=data.get("image_size", None),
            ocr_texts=ocr_texts,
            ocr_bboxes=ocr_bboxes,
            bbox_threshold=data.get("bbox_threshold", 0.05),
            iou_threshold=data.get("iou_threshold", None),
        )
        return {
            "image": annotated_image,
            "bboxes": filtered_bboxes_out,
        }

    def check_ocr_bboxes(
        self,
        image: ImageType,
        out_format: Literal["xywh", "xyxy"] = "xywh",
        ocr_kwargs: Optional[Dict[str, Any]] = {},
    ) -> Tuple[List[str], List[List[int]]]:
        if image.mode == "RBGA":
            image = image.convert("RGB")

        result = self.ocr.readtext(np.array(image), **ocr_kwargs)  # type: ignore
        texts = [str(item[1]) for item in result]
        bboxes = [
            self.coordinates_to_bbox(item[0], format=out_format) for item in result
        ]
        return (texts, bboxes)

    @staticmethod
    def coordinates_to_bbox(
        coordinates: np.ndarray, format: Literal["xywh", "xyxy"] = "xywh"
    ) -> List[int]:
        match format:
            case "xywh":
                return [
                    int(coordinates[0][0]),
                    int(coordinates[0][1]),
                    int(coordinates[2][0] - coordinates[0][0]),
                    int(coordinates[2][1] - coordinates[0][1]),
                ]
            case "xyxy":
                return [
                    int(coordinates[0][0]),
                    int(coordinates[0][1]),
                    int(coordinates[2][0]),
                    int(coordinates[2][1]),
                ]

    @staticmethod
    def bbox_area(bbox: List[int], w: int, h: int) -> int:
        bbox = [bbox[0] * w, bbox[1] * h, bbox[2] * w, bbox[3] * h]
        return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])

    @staticmethod
    def remove_bbox_overlap(
        xyxy_bboxes: List[Dict[str, Any]],
        ocr_bboxes: Optional[List[Dict[str, Any]]] = None,
        iou_threshold: Optional[float] = 0.7,
    ) -> List[Dict[str, Any]]:
        filtered_bboxes = []
        if ocr_bboxes is not None:
            filtered_bboxes.extend(ocr_bboxes)

        for i, bbox_outter in enumerate(xyxy_bboxes):
            bbox_left = bbox_outter["bbox"]
            valid_bbox = True

            for j, bbox_inner in enumerate(xyxy_bboxes):
                if i == j:
                    continue

                bbox_right = bbox_inner["bbox"]
                if (
                    intersection_over_union(
                        bbox_left,
                        bbox_right,
                    )
                    > iou_threshold  # type: ignore
                ) and (area(bbox_left) > area(bbox_right)):
                    valid_bbox = False
                    break

            if valid_bbox is False:
                continue

            if ocr_bboxes is None:
                filtered_bboxes.append(bbox_outter)
                continue

            box_added = False
            ocr_labels = []
            for ocr_bbox in ocr_bboxes:
                if not box_added:
                    bbox_right = ocr_bbox["bbox"]
                    if overlap(bbox_right, bbox_left):
                        try:
                            ocr_labels.append(ocr_bbox["content"])
                            filtered_bboxes.remove(ocr_bbox)
                        except Exception:
                            continue
                    elif overlap(bbox_left, bbox_right):
                        box_added = True
                        break

            if not box_added:
                filtered_bboxes.append(
                    {
                        "type": "icon",
                        "bbox": bbox_outter["bbox"],
                        "interactivity": True,
                        "content": " ".join(ocr_labels) if ocr_labels else None,
                    }
                )

        return filtered_bboxes

    def get_som_labeled_img(
        self,
        image: ImageType,
        image_size: Optional[Dict[Literal["w", "h"], int]] = None,
        ocr_texts: Optional[List[str]] = None,
        ocr_bboxes: Optional[List[List[int]]] = None,
        bbox_threshold: float = 0.01,
        iou_threshold: Optional[float] = None,
        caption_prompt: Optional[str] = None,
        caption_batch_size: int = 64,  # ~2GiB of GPU VRAM (can be increased to 128 which is ~4GiB of GPU VRAM)
    ) -> Tuple[str, List[Dict[str, Any]]]:
        if image.mode == "RBGA":
            image = image.convert("RGB")

        w, h = image.size
        if image_size is None:
            imgsz = {"h": h, "w": w}
        else:
            imgsz = [image_size.get("h", h), image_size.get("w", w)]

        out = self.yolo.predict(
            image,
            imgsz=imgsz,
            conf=bbox_threshold,
            iou=iou_threshold or 0.7,
            verbose=False,
        )[0]
        if out.boxes is None:
            raise RuntimeError(
                "YOLO prediction failed to produce the bounding boxes..."
            )

        xyxy_bboxes = out.boxes.xyxy
        xyxy_bboxes = xyxy_bboxes / torch.Tensor([w, h, w, h]).to(xyxy_bboxes.device)
        image_np = np.asarray(image)  # type: ignore

        if ocr_bboxes:
            ocr_bboxes = torch.tensor(ocr_bboxes) / torch.Tensor([w, h, w, h])  # type: ignore
            ocr_bboxes = ocr_bboxes.tolist()  # type: ignore

        ocr_bboxes = [
            {
                "type": "text",
                "bbox": bbox,
                "interactivity": False,
                "content": text,
                "source": "box_ocr_content_ocr",
            }
            for bbox, text in zip(ocr_bboxes, ocr_texts)  # type: ignore
            if self.bbox_area(bbox, w, h) > 0
        ]
        xyxy_bboxes = [
            {
                "type": "icon",
                "bbox": bbox,
                "interactivity": True,
                "content": None,
                "source": "box_yolo_content_yolo",
            }
            for bbox in xyxy_bboxes.tolist()
            if self.bbox_area(bbox, w, h) > 0
        ]

        filtered_bboxes = self.remove_bbox_overlap(
            xyxy_bboxes=xyxy_bboxes,
            ocr_bboxes=ocr_bboxes,  # type: ignore
            iou_threshold=iou_threshold or 0.7,
        )

        filtered_bboxes_out = sorted(
            filtered_bboxes, key=lambda x: x["content"] is None
        )
        starting_idx = next(
            (
                idx
                for idx, bbox in enumerate(filtered_bboxes_out)
                if bbox["content"] is None
            ),
            -1,
        )

        filtered_bboxes = torch.tensor([box["bbox"] for box in filtered_bboxes_out])
        non_ocr_bboxes = filtered_bboxes[starting_idx:]

        bbox_images = []
        for _, coordinates in enumerate(non_ocr_bboxes):
            try:
                xmin, xmax = (
                    int(coordinates[0] * image_np.shape[1]),
                    int(coordinates[2] * image_np.shape[1]),
                )
                ymin, ymax = (
                    int(coordinates[1] * image_np.shape[0]),
                    int(coordinates[3] * image_np.shape[0]),
                )
                cropped_image = image_np[ymin:ymax, xmin:xmax, :]
                cropped_image = cv2.resize(cropped_image, (64, 64))
                bbox_images.append(ToPILImage()(cropped_image))
            except Exception:
                continue

        if caption_prompt is None:
            caption_prompt = "<CAPTION>"

        captions = []
        for idx in range(0, len(bbox_images), caption_batch_size):  # type: ignore
            batch = bbox_images[idx : idx + caption_batch_size]  # type: ignore
            inputs = self.processor(
                images=batch,
                text=[caption_prompt] * len(batch),
                return_tensors="pt",
                do_resize=False,
            )
            if self.device.type in {"cuda", "mps"}:
                inputs = inputs.to(device=self.device, dtype=torch.float16)

            with torch.inference_mode():
                generated_ids = self.model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=20,
                    num_beams=1,
                    do_sample=False,
                    early_stopping=False,
                )

            generated_texts = self.processor.batch_decode(
                generated_ids, skip_special_tokens=True
            )
            captions.extend([text.strip() for text in generated_texts])

        ocr_texts = [f"Text Box ID {idx}: {text}" for idx, text in enumerate(ocr_texts)]  # type: ignore
        for _, bbox in enumerate(filtered_bboxes_out):
            if bbox["content"] is None:
                bbox["content"] = captions.pop(0)

        filtered_bboxes = box_convert(
            boxes=filtered_bboxes, in_fmt="xyxy", out_fmt="cxcywh"
        )

        annotated_image = image_np.copy()
        bboxes_annotate = filtered_bboxes * torch.Tensor([w, h, w, h])
        xyxy_annotate = box_convert(
            bboxes_annotate, in_fmt="cxcywh", out_fmt="xyxy"
        ).numpy()
        detections = Detections(xyxy=xyxy_annotate)
        labels = [str(idx) for idx in range(bboxes_annotate.shape[0])]

        annotated_image = self.annotator.annotate(
            scene=annotated_image,
            detections=detections,
            labels=labels,
            image_size=(w, h),
        )
        assert w == annotated_image.shape[1] and h == annotated_image.shape[0]

        out_image = Image.fromarray(annotated_image)
        out_buffer = io.BytesIO()
        out_image.save(out_buffer, format="PNG")
        encoded_image = base64.b64encode(out_buffer.getvalue()).decode("ascii")

        return encoded_image, filtered_bboxes_out


def area(bbox: List[int]) -> int:
    return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])


def intersection_area(bbox_left: List[int], bbox_right: List[int]) -> int:
    return max(
        0, min(bbox_left[2], bbox_right[2]) - min(bbox_left[0], bbox_right[0])
    ) * max(0, min(bbox_left[3], bbox_right[3]) - min(bbox_left[1], bbox_right[1]))


def intersection_over_union(bbox_left: List[int], bbox_right: List[int]) -> float:
    intersection = intersection_area(bbox_left, bbox_right)
    bbox_left_area = area(bbox_left)
    bbox_right_area = area(bbox_right)
    union = bbox_left_area + bbox_right_area - intersection + 1e-6

    ratio_left, ratio_right = 0, 0
    if bbox_left_area > 0 and bbox_right_area > 0:
        ratio_left = intersection / bbox_left_area
        ratio_right = intersection / bbox_right_area
    return max(intersection / union, ratio_left, ratio_right)


def overlap(bbox_left: List[int], bbox_right: List[int]) -> bool:
    intersection = intersection_area(bbox_left, bbox_right)
    ratio_left = intersection / area(bbox_left)
    return ratio_left > 0.80


class BoxAnnotator:
    def __init__(
        self,
        color: Union[Color, ColorPalette] = ColorPalette.DEFAULT,  # type: ignore
        thickness: int = 3,
        text_color: Color = Color.BLACK,  # type: ignore
        text_scale: float = 0.5,
        text_thickness: int = 2,
        text_padding: int = 10,
        avoid_overlap: bool = True,
    ):
        self.color: Union[Color, ColorPalette] = color
        self.thickness: int = thickness
        self.text_color: Color = text_color
        self.text_scale: float = text_scale
        self.text_thickness: int = text_thickness
        self.text_padding: int = text_padding
        self.avoid_overlap: bool = avoid_overlap

    def annotate(
        self,
        scene: np.ndarray,
        detections: Detections,
        labels: Optional[List[str]] = None,
        skip_label: bool = False,
        image_size: Optional[Tuple[int, int]] = None,
    ) -> np.ndarray:
        font = cv2.FONT_HERSHEY_SIMPLEX
        for i in range(len(detections)):
            x1, y1, x2, y2 = detections.xyxy[i].astype(int)
            class_id = (
                detections.class_id[i] if detections.class_id is not None else None
            )
            idx = class_id if class_id is not None else i
            color = (
                self.color.by_idx(idx)
                if isinstance(self.color, ColorPalette)
                else self.color
            )
            cv2.rectangle(
                img=scene,
                pt1=(x1, y1),
                pt2=(x2, y2),
                color=color.as_bgr(),
                thickness=self.thickness,
            )
            if skip_label:
                continue

            text = (
                f"{class_id}"
                if (labels is None or len(detections) != len(labels))
                else labels[i]
            )

            text_width, text_height = cv2.getTextSize(
                text=text,
                fontFace=font,
                fontScale=self.text_scale,
                thickness=self.text_thickness,
            )[0]

            if not self.avoid_overlap:
                text_x = x1 + self.text_padding
                text_y = y1 - self.text_padding

                text_background_x1 = x1
                text_background_y1 = y1 - 2 * self.text_padding - text_height

                text_background_x2 = x1 + 2 * self.text_padding + text_width
                text_background_y2 = y1
            else:
                (
                    text_x,
                    text_y,
                    text_background_x1,
                    text_background_y1,
                    text_background_x2,
                    text_background_y2,
                ) = self.get_optimal_label_pos(
                    self.text_padding,
                    text_width,
                    text_height,
                    x1,
                    y1,
                    x2,
                    y2,
                    detections,
                    image_size,
                )

            cv2.rectangle(
                img=scene,
                pt1=(text_background_x1, text_background_y1),
                pt2=(text_background_x2, text_background_y2),
                color=color.as_bgr(),
                thickness=cv2.FILLED,
            )
            box_color = color.as_rgb()
            luminance = (
                0.299 * box_color[0] + 0.587 * box_color[1] + 0.114 * box_color[2]
            )
            text_color = (0, 0, 0) if luminance > 160 else (255, 255, 255)
            cv2.putText(
                img=scene,
                text=text,
                org=(text_x, text_y),
                fontFace=font,
                fontScale=self.text_scale,
                color=text_color,
                thickness=self.text_thickness,
                lineType=cv2.LINE_AA,
            )
        return scene

    @staticmethod
    def get_optimal_label_pos(
        text_padding, text_width, text_height, x1, y1, x2, y2, detections, image_size
    ):
        def get_is_overlap(
            detections,
            text_background_x1,
            text_background_y1,
            text_background_x2,
            text_background_y2,
            image_size,
        ):
            is_overlap = False
            for i in range(len(detections)):
                detection = detections.xyxy[i].astype(int)
                if (
                    intersection_over_union(
                        [
                            text_background_x1,
                            text_background_y1,
                            text_background_x2,
                            text_background_y2,
                        ],
                        detection,
                    )
                    > 0.3
                ):
                    is_overlap = True
                    break
            if (
                text_background_x1 < 0
                or text_background_x2 > image_size[0]
                or text_background_y1 < 0
                or text_background_y2 > image_size[1]
            ):
                is_overlap = True
            return is_overlap

        text_x = x1 + text_padding
        text_y = y1 - text_padding

        text_background_x1 = x1
        text_background_y1 = y1 - 2 * text_padding - text_height

        text_background_x2 = x1 + 2 * text_padding + text_width
        text_background_y2 = y1
        is_overlap = get_is_overlap(
            detections,
            text_background_x1,
            text_background_y1,
            text_background_x2,
            text_background_y2,
            image_size,
        )
        if not is_overlap:
            return (
                text_x,
                text_y,
                text_background_x1,
                text_background_y1,
                text_background_x2,
                text_background_y2,
            )

        text_x = x1 - text_padding - text_width
        text_y = y1 + text_padding + text_height

        text_background_x1 = x1 - 2 * text_padding - text_width
        text_background_y1 = y1

        text_background_x2 = x1
        text_background_y2 = y1 + 2 * text_padding + text_height
        is_overlap = get_is_overlap(
            detections,
            text_background_x1,
            text_background_y1,
            text_background_x2,
            text_background_y2,
            image_size,
        )
        if not is_overlap:
            return (
                text_x,
                text_y,
                text_background_x1,
                text_background_y1,
                text_background_x2,
                text_background_y2,
            )

        text_x = x2 + text_padding
        text_y = y1 + text_padding + text_height

        text_background_x1 = x2
        text_background_y1 = y1

        text_background_x2 = x2 + 2 * text_padding + text_width
        text_background_y2 = y1 + 2 * text_padding + text_height

        is_overlap = get_is_overlap(
            detections,
            text_background_x1,
            text_background_y1,
            text_background_x2,
            text_background_y2,
            image_size,
        )
        if not is_overlap:
            return (
                text_x,
                text_y,
                text_background_x1,
                text_background_y1,
                text_background_x2,
                text_background_y2,
            )

        text_x = x2 - text_padding - text_width
        text_y = y1 - text_padding

        text_background_x1 = x2 - 2 * text_padding - text_width
        text_background_y1 = y1 - 2 * text_padding - text_height

        text_background_x2 = x2
        text_background_y2 = y1

        is_overlap = get_is_overlap(
            detections,
            text_background_x1,
            text_background_y1,
            text_background_x2,
            text_background_y2,
            image_size,
        )
        if not is_overlap:
            return (
                text_x,
                text_y,
                text_background_x1,
                text_background_y1,
                text_background_x2,
                text_background_y2,
            )

        return (
            text_x,
            text_y,
            text_background_x1,
            text_background_y1,
            text_background_x2,
            text_background_y2,
        )