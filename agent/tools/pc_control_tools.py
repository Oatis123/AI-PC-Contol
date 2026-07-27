import subprocess
import winreg
import shlex
import psutil
import time
import ctypes
import logging
from typing import List, Dict, Any, Union, Optional

from langchain_core.tools import tool

_SOFTWARE_CACHE = None
_SOFTWARE_CACHE_TIME = 0
_MODERN_APPS_CACHE = {}
_CLASSIC_APP_PATHS_CACHE = None
_CLASSIC_APP_PATHS_CACHE_TIME = 0
from pywinauto import Desktop
from pywinauto.application import Application
from pywinauto.findwindows import ElementNotFoundError
from PIL import ImageGrab

import pyautogui

ELEMENTS_CACHE = {}
CURRENT_ID = 0

def _get_installed_software():
    global _SOFTWARE_CACHE, _SOFTWARE_CACHE_TIME, _MODERN_APPS_CACHE
    if _SOFTWARE_CACHE is not None and (time.time() - _SOFTWARE_CACHE_TIME < 300):
        return _SOFTWARE_CACHE

    all_apps = set()

    command_classic = r'''
    Get-ItemProperty HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*, 
                     HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*, 
                     HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* | 
    Where-Object {$_.PSObject.Properties['DisplayName'] -and $_.DisplayName -ne $null} |
    Select-Object -ExpandProperty DisplayName
    '''
    
    result_classic = subprocess.run(["powershell", "-Command", command_classic], capture_output=True, text=True, encoding='utf-8', errors='ignore')

    if result_classic.returncode == 0:
        classic_apps = {line.strip() for line in result_classic.stdout.splitlines() if line.strip()}
        all_apps.update(classic_apps)

    command_modern = r'Get-AppxPackage | ForEach-Object { "$($_.Name)|$($_.PackageFamilyName)" }'
    result_modern = subprocess.run(["powershell", "-Command", command_modern], capture_output=True, text=True, encoding='utf-8', errors='ignore')
    
    if result_modern.returncode == 0:
        for line in result_modern.stdout.splitlines():
            line = line.strip()
            if '|' in line:
                name, pfn = line.split('|', 1)
                _MODERN_APPS_CACHE[name.lower()] = pfn
                all_apps.add(name)

    full_list = sorted(list(all_apps))
    
    stop_words = [
        'sdk', 'driver', 'redistributable', 'runtime', 'update', 
        'package', 'microsoft .net', 'visual c++', 'prerequisites',
        'manifest', 'host', 'tools', 'amd', 'nvidia', 'intel', 
        'microsoft.windows', 'microsoft.vclibs', 'microsoft.ui',
        'microsoft.web', 'microsoft.aspnet', 'microsoft.testplatform',
        'vs_', 'windows sdk', 'debugger', 'targeting', 'interop'
    ]

    filtered_list = [
        app for app in full_list 
        if not any(stop_word.lower() in app.lower() for stop_word in stop_words)
    ]
    
    _SOFTWARE_CACHE = filtered_list
    _SOFTWARE_CACHE_TIME = time.time()
    return filtered_list


@tool
def get_installed_software():
    """Возвращает отфильтрованный список установленных программ, исключая системные компоненты. Не принимает аргументов."""
    return _get_installed_software()


@tool
def find_application_name(approximate_name: str) -> str:
    """
    Находит точное название установленного приложения по его примерному названию.

    Args:
        approximate_name (str): Приблизительное имя приложения для поиска (например, "chrome").
    """
    all_apps = _get_installed_software()
    
    search_term = approximate_name.lower()
    
    for app_name in all_apps:
        if search_term in app_name.lower():
            return app_name
    
    return f"Ошибка: Приложение '{approximate_name}' не найдено среди установленных программ."


def _get_classic_app_paths():
    global _CLASSIC_APP_PATHS_CACHE, _CLASSIC_APP_PATHS_CACHE_TIME
    if _CLASSIC_APP_PATHS_CACHE is not None and (time.time() - _CLASSIC_APP_PATHS_CACHE_TIME < 300):
        return _CLASSIC_APP_PATHS_CACHE

    app_paths = {}
    registry_paths = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
    ]

    for hkey in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
        for path in registry_paths:
            try:
                key = winreg.OpenKey(hkey, path, 0, winreg.KEY_READ)
                for i in range(winreg.QueryInfoKey(key)[0]):
                    subkey_name = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, subkey_name) as subkey:
                        display_name, install_location, display_icon = None, None, None
                        try:
                            display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                        except OSError:
                            pass
                        try:
                            display_icon = winreg.QueryValueEx(subkey, "DisplayIcon")[0]
                        except OSError:
                            pass
                        try:
                            install_location = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                        except OSError:
                            pass
                        
                        executable_path = None
                        if display_icon:
                            executable_path = display_icon.split(',')[0].strip('"')
                        elif install_location:
                            executable_path = install_location.strip('"')

                        if display_name and executable_path:
                            app_paths[display_name.lower()] = executable_path
            except FileNotFoundError:
                pass
    _CLASSIC_APP_PATHS_CACHE = app_paths
    _CLASSIC_APP_PATHS_CACHE_TIME = time.time()
    return app_paths


def _start_application_by_name(app_name: str) -> bool:
    app_name_lower = app_name.lower()

    try:
        classic_app_map = _get_classic_app_paths()
        for name, path in classic_app_map.items():
            if app_name_lower in name:
                subprocess.Popen(shlex.split(f'"{path}"'))
                time.sleep(2.0)
                return True
    except Exception as e:
        print(f"Ошибка при поиске в реестре: {e}")

    try:
        if not _MODERN_APPS_CACHE:
            _get_installed_software()
            
        found_pfn = None
        for name, pfn in _MODERN_APPS_CACHE.items():
            if app_name_lower in name:
                found_pfn = pfn
                break
                
        if found_pfn:
            launch_command = f'explorer.exe shell:appsFolder\\{found_pfn}!App'
            subprocess.Popen(launch_command, shell=True)
            time.sleep(2.0)
            return True
    except Exception as e:
        print(f"Ошибка при поиске современных приложений: {e}")

    try:
        simple_name = app_name_lower.split(' ')[0]
        subprocess.Popen(f'start {simple_name}', shell=True)
        time.sleep(2.0)
        return True
    except Exception as e:
        print(f"Простой запуск не удался: {e}")

    print(f"Не удалось найти и запустить приложение: '{app_name}'")
    return False


@tool
def start_application(app_name: str)->bool:
    """Запускает приложение по его точному названию. Используйте `find_application_name`, чтобы найти его. Возвращает True при успехе."""
    return _start_application_by_name(app_name=app_name)


@tool
def get_open_windows():
    """Возвращает список заголовков всех открытых окон."""
    EnumWindows = ctypes.windll.user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
    GetWindowText = ctypes.windll.user32.GetWindowTextW
    GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
    IsWindowVisible = ctypes.windll.user32.IsWindowVisible
    
    window_titles = []
    def foreach_window(hwnd, lParam):
        if IsWindowVisible(hwnd):
            length = GetWindowTextLength(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                GetWindowText(hwnd, buff, length + 1)
                title = buff.value
                if title:
                    window_titles.append(title)
        return True
        
    EnumWindows(EnumWindowsProc(foreach_window), 0)
    
    if not window_titles:
        return "не найдено открытых окон"
    
    return "\n".join(window_titles)

@tool
def scrape_application(name: str) -> str:
    """
    Сканирует окно приложения с помощью визуального нейросетевого парсера OmniParser.
    Делает скриншот окна приложения, детектует активные элементы (кнопки, иконки, поля ввода, текст)
    и присваивает каждому элементу уникальный 'id' для взаимодействия.

    Args:
        name (str): Часть заголовка окна для поиска.
    """
    start_time = time.time()
    global ELEMENTS_CACHE, CURRENT_ID
    ELEMENTS_CACHE.clear()
    CURRENT_ID = 0

    logging.info(f"🔍 [OmniParser] Поиск окна для скрапинга: '{name}'...")

    try:
        app_name = name.split(" - ")[-1].strip() if " - " in name else name.strip()
        
        main_win_spec = Desktop(backend="win32").window(title_re=f".*{app_name}.*", found_index=0)
        if not main_win_spec.exists(timeout=0.5):
            main_win_spec = Desktop(backend="uia").window(title_re=f".*{app_name}.*", found_index=0)
            if not main_win_spec.exists(timeout=0.5):
                elapsed = time.time() - start_time
                logging.info(f"❌ [OmniParser] Окно '{name}' не найдено ({elapsed:.4f} сек).")
                return f"Ошибка: Окно с именем, содержащим '{name}', не найдено."

        main_win = main_win_spec.wrapper_object()

        if not main_win.is_active():
            logging.info(f"↗️ [OmniParser] Фокусировка на окне '{app_name}'...")
            try:
                main_win.set_focus()
            except Exception:
                pass

        rect = main_win.rectangle()
        if rect.width() <= 0 or rect.height() <= 0:
            logging.warning(f"⚠️ [OmniParser] Окно '{app_name}' имеет нулевой размер {rect}.")
            return "Ошибка: Окно имеет нулевой размер или свернуто."

        # Capture window screenshot
        bbox = (rect.left, rect.top, rect.right, rect.bottom)
        logging.info(f"📸 [OmniParser] Захват скриншота области окна: X={rect.left}, Y={rect.top}, W={rect.width()}, H={rect.height()}...")
        img = ImageGrab.grab(bbox=bbox)

        # Parse screenshot with OmniParser engine
        logging.info(f"🧠 [OmniParser] Запуск распознавания элементов (YOLO + OCR)...")
        from agent.vision.omniparser_engine import OmniParserEngine
        engine = OmniParserEngine()
        elements = engine.parse_image(img)

        if not elements:
            logging.warning(f"⚠️ [OmniParser] Элементы не найдены на скриншоте '{app_name}'.")
            return "OmniParser не обнаружил интерактивных элементов на скриншоте окна."

        xml_lines = ["<WindowVision>"]
        for elem in elements:
            elem_id = CURRENT_ID
            # Map window-relative coordinates to absolute screen coordinates
            abs_left = rect.left + elem["left"]
            abs_top = rect.top + elem["top"]
            abs_right = rect.left + elem["right"]
            abs_bottom = rect.top + elem["bottom"]

            ELEMENTS_CACHE[elem_id] = {
                "left": abs_left, "top": abs_top,
                "right": abs_right, "bottom": abs_bottom,
                "name": elem["name"],
                "control_type": elem["control_type"]
            }
            CURRENT_ID += 1

            safe_name = elem["name"].replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            tag = elem["control_type"].replace("/", "_")
            # Include spatial coordinates hint so LLM understands layout (row/column position)
            rel_cx = (elem["left"] + elem["right"]) // 2
            rel_cy = (elem["top"] + elem["bottom"]) // 2
            xml_lines.append(f'  <{tag} id="{elem_id}" name="{safe_name}" pos="x:{rel_cx},y:{rel_cy}" />')

        xml_lines.append("</WindowVision>")
        xml_output = "\n".join(xml_lines)

        elapsed = time.time() - start_time
        logging.info(f"⏱️ [OmniParser] Окно '{name}' успешно распознано за {elapsed:.4f} сек. Найдено {CURRENT_ID} элементов!")
        return xml_output

    except ElementNotFoundError:
        elapsed = time.time() - start_time
        logging.info(f"❌ [OmniParser] Окно '{name}' не найдено ({elapsed:.4f} сек).")
        return f"Произошла ошибка: Окно с именем '{name}' не найдено после ожидания."
    except Exception as e:
        logging.error(f"💥 [OmniParser] Ошибка при сканировании окна '{name}': {e}", exc_info=True)
        return f"Произошла ошибка скрапинга OmniParser: {e}"
    

@tool
def interact_with_element_by_id(
    name: str,
    element_id: int = -1,
    action: str = None,
    text_to_set: Optional[str] = None
) -> Union[str, Any]:
    """
    Находит UI-элемент по его точным координатам в указанном окне и выполняет над ним определённое действие.

    Args:
        name (str): Часть заголовка окна приложения для поиска. Например, 'Mozilla Firefox' или 'Калькулятор'.
        element (int): ID целевого элемента, полученный от `scrape_application`. 
        action (str): Действие, которое необходимо выполнить над элементом. Поддерживаемые действия:
                      - Клики: 'click', 'double_click', 'right_click'.
                      - Работа с текстом: 'set_text', 'get_text', 'press_enter'.
                      - Прокрутка: 'scroll_up', 'scroll_down', 'scroll_left', 'scroll_right'.
                      - Масштабирование (применяется ко всему окну): 'zoom_in', 'zoom_out'.
        text_to_set (Optional[str]): Текстовая строка для ввода. Является обязательным аргументом только для действия 'set_text'.

    Returns:
        Union[str, Any]:
            - В случае успеха для большинства действий — строка с сообщением об успехе (например, "Действие 'click' успешно выполнено.").
            - Для действия 'get_text' — текст, содержащийся в элементе.
            - В случае ошибки — строка с описанием ошибки (например, "Ошибка: Элемент с координатами ... не найден.").
    """
    try:
        if element_id not in ELEMENTS_CACHE and 'zoom' not in action and action not in ["type_text_blind", "press_enter"]:
            return f"Ошибка: Элемент с id {element_id} не найден в кэше."

        if element_id in ELEMENTS_CACHE:
            el_data = ELEMENTS_CACHE[element_id]
            center_x = (el_data['left'] + el_data['right']) // 2
            center_y = (el_data['top'] + el_data['bottom']) // 2
        else:
            center_x, center_y = 0, 0

        action = action.lower()
        if action == 'click':
            pyautogui.click(center_x, center_y)
        elif action == 'double_click':
            pyautogui.doubleClick(center_x, center_y)
        elif action == 'right_click':
            pyautogui.rightClick(center_x, center_y)
        
        elif action == 'set_text':
            if text_to_set is None:
                return "Ошибка: для действия 'set_text' необходимо передать аргумент 'text_to_set'."
            
            # 1. Кликаем по элементу — это выведет окно на передний план
            pyautogui.click(center_x, center_y)
            time.sleep(0.15)

            # 2. Определяем нужный язык и переключаем раскладку активного окна
            import ctypes
            
            # Проверяем, есть ли хоть одна русская буква
            has_cyrillic = any('а' <= c <= 'я' or 'А' <= c <= 'Я' or c in 'ёЁ' for c in text_to_set)
            # 0x0419 = Русский, 0x0409 = Английский (США)
            hkl = 0x0419 if has_cyrillic else 0x0409
            
            # Берём хэндл окна, которое сейчас в фокусе (после нашего клика)
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            # Посылаем системное сообщение WM_INPUTLANGCHANGEREQUEST (0x0050)
            ctypes.windll.user32.PostMessageW(hwnd, 0x0050, 0, hkl)
            
            # ВАЖНО: Винда меняет раскладку асинхронно, даём ей 200мс на подумать
            time.sleep(0.2)

            # 3. Стираем старое содержимое
            pyautogui.hotkey('ctrl', 'a')
            pyautogui.press('delete')
            time.sleep(0.1)

            # 4. Вводим текст — теперь pyautogui попадет по нужным скан-кодам
            pyautogui.write(text_to_set, interval=0.01)
            
            return f"Действие '{action}' успешно выполнено."
            
        elif action == "type_text_blind":
            if not text_to_set:
                return "Ошибка: нужен text_to_set."

            main_win.set_focus()
            pyautogui.write(text_to_set, interval=0.01)
            
        elif action == 'press_enter':
            # Вместо клика по элементу, жестко активируем само окно
            main_win.set_focus()
            time.sleep(0.1)
            
            # Вариант А: Отправляем Enter через встроенный синтаксис pywinauto
            main_win.type_keys('~')
            
        elif action == 'get_text':
            return ELEMENTS_CACHE.get(element_id, {}).get("name", "")

        elif action == 'scroll_up':
            pyautogui.moveTo(center_x, center_y)
            pyautogui.scroll(500)
        elif action == 'scroll_down':
            pyautogui.moveTo(center_x, center_y)
            pyautogui.scroll(-500)
        elif action == 'scroll_left':
            pyautogui.moveTo(center_x, center_y)
            pyautogui.hscroll(-500)
        elif action == 'scroll_right':
            pyautogui.moveTo(center_x, center_y)
            pyautogui.hscroll(500)

        elif action == 'zoom_in':
            main_win.type_keys('^{PLUS}')
        elif action == 'zoom_out':
            main_win.type_keys('^{MINUS}')
            
        else:
            return f"Ошибка: Неизвестное действие '{action}'."
        
        return f"Действие '{action}' успешно выполнено."

    except ElementNotFoundError:
        return f"Ошибка: Окно с именем '{name}' не найдено."
    except Exception as e:
        return f"Произошла непредвиденная ошибка: {type(e).__name__}: {e}"
    

@tool
def execute_bash_command(command: str) -> str:
    """
    Выполняет команду в терминале (shell) и возвращает ее вывод. Запрещены разрушительные команды.

    Args:
        command (str): Команда для выполнения.
    """
    timeout_seconds = 10
    
    try:
        result = subprocess.run(
            command, 
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            encoding="utf-8",
            errors="replace"
        )

        if result.returncode != 0:
            return f"Ошибка выполнения команды:\nСтатус код: {result.returncode}\nStderr: {result.stderr}"
        
        if not result.stdout.strip():
            return "Команда выполнена успешно, но не произвела вывода (stdout)."

        return f"Результат выполнения:\n{result.stdout}"

    except FileNotFoundError:
        return f"Ошибка: команда '{command.split()[0]}' не найдена. Убедись, что она установлена и доступна в PATH."
    except subprocess.TimeoutExpired:
        return f"Ошибка: выполнение команды превысило тайм-аут в {timeout_seconds} секунд."
    except Exception as e:
        return f"Произошла непредвиденная ошибка: {str(e)}"