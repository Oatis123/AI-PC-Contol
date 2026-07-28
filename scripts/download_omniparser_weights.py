import os
import logging
from huggingface_hub import snapshot_download

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

WEIGHTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", "vision", "weights")

def download_weights():
    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    logging.info(f"Downloading OmniParser weights into {WEIGHTS_DIR}...")
    try:
        # Download from official Microsoft OmniParser repo
        local_dir = snapshot_download(
            repo_id="microsoft/OmniParser-v2.0",
            local_dir=WEIGHTS_DIR,
            ignore_patterns=["*.git*", "README.md"]
        )
        logging.info(f"OmniParser weights successfully downloaded to: {local_dir}")
        return local_dir
    except Exception as e:
        logging.warning(f"Failed to download OmniParser-v2.0, trying fallback microsoft/OmniParser: {e}")
        try:
            local_dir = snapshot_download(
                repo_id="microsoft/OmniParser",
                local_dir=WEIGHTS_DIR,
                ignore_patterns=["*.git*", "README.md"]
            )
            logging.info(f"OmniParser weights successfully downloaded to: {local_dir}")
            return local_dir
        except Exception as ex:
            logging.error(f"Error downloading OmniParser weights: {ex}")
            raise ex

if __name__ == "__main__":
    download_weights()
