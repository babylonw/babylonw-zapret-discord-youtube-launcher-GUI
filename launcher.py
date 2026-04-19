import os
import sys
import shutil
import subprocess
import threading
import re
import tkinter as tk
from tkinter import Tk, Label, Button, StringVar, OptionMenu, Text, Scrollbar, END, Frame, Entry, messagebox, Checkbutton, BooleanVar
import ctypes
import xml.etree.ElementTree as ET
import time
import requests
import ssl
import socket
import webbrowser

# ------------------------------------------------------------
# 1. Функции для распаковки, патча BAT, прав, рабочей папки
# ------------------------------------------------------------

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_as_admin():
    script = os.path.abspath(sys.argv[0])
    params = ' '.join(sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", script, params, None, 1)
    sys.exit()

def get_working_dir():
    return os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ZapretBundle')

def extract_resources():
    if getattr(sys, 'frozen', False):
        src = os.path.join(sys._MEIPASS, 'zapret_files')
    else:
        src = os.path.join(os.path.dirname(__file__), 'zapret_files')
    dst = get_working_dir()
    if not os.path.exists(dst):
        os.makedirs(dst)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            if os.path.exists(d):
                shutil.rmtree(d)
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    return dst

def patch_bat_files(working_dir):
    pattern = re.compile(r'(start\s+"[^"]*"\s+)/min\s+', re.IGNORECASE)
    for file in os.listdir(working_dir):
        if file.endswith('.bat') and 'general' in file.lower():
            path = os.path.join(working_dir, file)
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            new_content = pattern.sub(r'\1/b ', content)
            if new_content != content:
                with open(path, 'w', encoding='utf-8', newline='\r\n') as f:
                    f.write(new_content)

def create_desktop_shortcut(target_exe):
    desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
    shortcut_path = os.path.join(desktop, "Zapret Launcher.lnk")
    if os.path.exists(shortcut_path):
        return shortcut_path
    target_esc = target_exe.replace('\\', '\\\\').replace('"', '\\"')
    wd_esc = os.path.dirname(target_exe).replace('\\', '\\\\').replace('"', '\\"')
    ps_code = f'''
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
    $Shortcut.TargetPath = "{target_esc}"
    $Shortcut.Description = "Zapret Launcher"
    $Shortcut.WorkingDirectory = "{wd_esc}"
    $Shortcut.Save()
    '''
    try:
        subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_code],
            capture_output=True, check=True, shell=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except:
        pass
    return shortcut_path

# ------------------------------------------------------------
# 2. Функции для автозапуска через планировщик задач
# ------------------------------------------------------------

TASK_NAME = "ZapretAutostart"

def set_autostart(bat_path, enable):
    if enable:
        cmd = f'cmd /c start /b "{bat_path}"'
        subprocess.run([
            "schtasks", "/create", "/tn", TASK_NAME, "/tr", cmd,
            "/sc", "onstart", "/rl", "highest", "/f"
        ], capture_output=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"], capture_output=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW)

def is_autostart_enabled():
    result = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return result.returncode == 0

def get_autostart_bat_path():
    result = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME, "/xml"], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        return None
    try:
        root = ET.fromstring(result.stdout)
        for elem in root.iter():
            if elem.tag.endswith('Command'):
                cmd_line = elem.text
                match = re.search(r'start\s+/b\s+"([^"]+)"', cmd_line)
                if match:
                    return match.group(1)
    except:
        pass
    return None

# ------------------------------------------------------------
# 3. Функции для проверки стратегии (скрытые окна)
# ------------------------------------------------------------

def ping_host_simple(hostname, count=4):
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        result = subprocess.run(
            ["ping", "-n", str(count), hostname],
            capture_output=True, timeout=10,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        stdout = result.stdout.decode('cp866', errors='replace')
        for line in stdout.splitlines():
            if "Среднее" in line or "Average" in line:
                numbers = re.findall(r'\d+', line)
                if numbers:
                    return int(numbers[-1])
        return None
    except:
        return None

def check_http(url):
    try:
        r = requests.get(url, timeout=5, allow_redirects=True)
        if r.status_code == 200:
            return "OK"
        else:
            return f"HTTP{r.status_code}"
    except:
        return "FAIL"

def check_tls_version(hostname, port=443, version='1.2'):
    try:
        context = ssl.create_default_context()
        if version == '1.2':
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.maximum_version = ssl.TLSVersion.TLSv1_2
        elif version == '1.3':
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.maximum_version = ssl.TLSVersion.TLSv1_3
        with socket.create_connection((hostname, port), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                return "OK"
    except:
        return "FAIL"

def stop_zapret(output_text):
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        result = subprocess.run(
            ["taskkill", "/f", "/im", "winws.exe"],
            capture_output=True, text=False,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        stdout_text = result.stdout.decode('cp866', errors='replace')
        stderr_text = result.stderr.decode('cp866', errors='replace') if result.stderr else ""
        output_text.insert(END, f"[INFO] {stdout_text}\n")
        if stderr_text:
            output_text.insert(END, f"[WARN] {stderr_text}\n")
        output_text.see(END)
    except Exception as e:
        output_text.insert(END, f"[ERROR] {e}\n")
        output_text.see(END)

def ping_host(host_entry, output_text):
    host = host_entry.get().strip()
    if not host:
        host = "discord.com"
        host_entry.delete(0, END)
        host_entry.insert(0, host)
    output_text.insert(END, f"[PING] Проверка связи с {host}...\n")
    output_text.see(END)

    def ping_thread():
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            result = subprocess.run(
                ["ping", "-n", "4", host],
                capture_output=True, timeout=10,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            stdout_text = result.stdout.decode('cp866', errors='replace')
            stderr_text = result.stderr.decode('cp866', errors='replace') if result.stderr else ""
            output_text.insert(END, stdout_text + "\n")
            if stderr_text:
                output_text.insert(END, stderr_text + "\n")
            output_text.see(END)
        except Exception as e:
            output_text.insert(END, f"[ERROR] Ошибка пинга: {e}\n")
            output_text.see(END)

    threading.Thread(target=ping_thread, daemon=True).start()

def test_selected_strategy(working_dir, strategy_var, output_text, test_button):
    def run():
        test_button.config(state=tk.DISABLED, text="Проверка...")
        try:
            strategy = strategy_var.get()
            if not strategy or strategy == "Стратегии не найдены":
                output_text.insert(END, "[ERROR] Не выбрана стратегия для проверки\n")
                output_text.see(END)
                return
            bat_path = os.path.join(working_dir, strategy)
            if not os.path.exists(bat_path):
                output_text.insert(END, f"[ERROR] Файл {bat_path} не найден\n")
                output_text.see(END)
                return

            output_text.insert(END, f"\n[INFO] Начата проверка стратегии: {strategy}\n")
            output_text.insert(END, "[INFO] Пожалуйста, дождитесь завершения процесса...\n\n")
            output_text.see(END)

            stop_zapret(output_text)

            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            subprocess.Popen(
                [bat_path],
                cwd=working_dir,
                shell=True,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            time.sleep(3)

            targets = [
                ("DiscordMain", "https://discord.com", "discord.com"),
                ("DiscordGateway", "https://gateway.discord.gg", "gateway.discord.gg"),
                ("DiscordCDN", "https://cdn.discordapp.com", "cdn.discordapp.com"),
                ("DiscordUpdates", "https://updates.discord.com", "updates.discord.com"),
                ("YouTubeWeb", "https://www.youtube.com", "www.youtube.com"),
                ("YouTubeShort", "https://youtu.be", "youtu.be"),
                ("YouTubeImage", "https://i.ytimg.com", "i.ytimg.com"),
                ("YouTubeVideoRedirect", "https://redirector.googlevideo.com", "redirector.googlevideo.com"),
                ("GoogleMain", "https://www.google.com", "www.google.com"),
                ("GoogleGstatic", "https://www.gstatic.com", "www.gstatic.com"),
                ("CloudflareWeb", "https://www.cloudflare.com", "www.cloudflare.com"),
                ("CloudflareCDN", "https://cdnjs.cloudflare.com", "cdnjs.cloudflare.com"),
            ]
            dns_targets = [
                ("CloudflareDNS1111", "1.1.1.1"),
                ("CloudflareDNS1001", "1.0.0.1"),
                ("GoogleDNS8888", "8.8.8.8"),
                ("GoogleDNS8844", "8.8.4.4"),
                ("Quad9DNS9999", "9.9.9.9"),
            ]

            output_text.insert(END, "> Выполнение проверки...\n")
            output_text.see(END)

            for name, url, host in targets:
                http = check_http(url)
                tls12 = check_tls_version(host, 443, '1.2')
                tls13 = check_tls_version(host, 443, '1.3')
                ping = ping_host_simple(host, 4)
                ping_str = f"{ping} ms" if ping else "Timeout"
                output_text.insert(END, f"{name:<20} HTTP:{http:<4} TLS1.2:{tls12:<4} TLS1.3:{tls13:<4} | Ping: {ping_str}\n")
                output_text.see(END)

            for name, ip in dns_targets:
                ping = ping_host_simple(ip, 4)
                ping_str = f"{ping} ms" if ping else "Timeout"
                output_text.insert(END, f"{name:<20} Ping: {ping_str}\n")
                output_text.see(END)

            output_text.insert(END, "> Проверка завершена. Стратегия продолжает работу.\n")
            output_text.see(END)
        except Exception as e:
            output_text.insert(END, f"[ERROR] Непредвиденная ошибка: {e}\n")
            output_text.see(END)
        finally:
            test_button.config(state=tk.NORMAL, text="Проверить стратегию")

    threading.Thread(target=run, daemon=True).start()

def run_service_bat(working_dir, output_text):
    service_path = os.path.join(working_dir, "service.bat")
    if os.path.exists(service_path):
        output_text.insert(END, f"[INFO] Запуск service.bat...\n")
        output_text.see(END)
        subprocess.Popen([service_path], cwd=working_dir, shell=True)
    else:
        output_text.insert(END, f"[ERROR] Файл service.bat не найден в {working_dir}\n")
        output_text.see(END)

def open_telegram_channel():
    webbrowser.open("https://t.me/zapret_discord_youtube")

def open_github():
    webbrowser.open("https://github.com/Flowseal/zapret-discord-youtube")

# ------------------------------------------------------------
# 4. GUI с переключением тем и исправленным автозапуском
# ------------------------------------------------------------

def apply_theme(root, output_text, theme):
    if theme == "dark":
        bg_color = "#2d2d2d"
        fg_color = "#ffffff"
        text_bg = "#1e1e1e"
        text_fg = "#d4d4d4"
        button_bg = "#3c3f41"
        entry_bg = "#404040"
    else:
        bg_color = "#f0f0f0"
        fg_color = "#000000"
        text_bg = "#ffffff"
        text_fg = "#000000"
        button_bg = "#e1e1e1"
        entry_bg = "#ffffff"

    root.configure(bg=bg_color)
    output_text.configure(bg=text_bg, fg=text_fg, insertbackground=fg_color)

    def configure_widgets(widget):
        try:
            if 'bg' in widget.keys():
                widget.configure(bg=bg_color)
            if 'fg' in widget.keys() and not isinstance(widget, Text):
                widget.configure(fg=fg_color)
            if isinstance(widget, Button):
                widget.configure(bg=button_bg)
            if isinstance(widget, Entry):
                widget.configure(bg=entry_bg, insertbackground=fg_color)
        except:
            pass
        for child in widget.winfo_children():
            configure_widgets(child)

    configure_widgets(root)

def main_gui(working_dir):
    root = Tk()
    root.title("Zapret Launcher GUI")
    root.geometry("750x620")
    root.configure(bg="#f0f0f0")

    theme_var = StringVar(root)
    theme_var.set("light")

    Label(root, text="Выберите стратегию обхода:").pack(pady=5)

    strategies = [f for f in os.listdir(working_dir) if f.endswith('.bat') and 'general' in f.lower()]
    if not strategies:
        strategies = ["Стратегии не найдены"]

    strategy_var = StringVar(root)
    strategy_var.set(strategies[0])

    autostart_var = BooleanVar(root)
    autostart_updating = False

    def run_zapret():
        strategy = strategy_var.get()
        if not strategy or strategy == "Стратегии не найдены":
            return
        bat_path = os.path.join(working_dir, strategy)
        if os.path.exists(bat_path):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            subprocess.Popen(
                [bat_path],
                cwd=working_dir,
                shell=True,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            output_text.insert(END, f"[INFO] Запущена стратегия: {strategy} (фоновый режим)\n")
            output_text.see(END)

    def stop():
        stop_zapret(output_text)

    def test_strategy():
        test_selected_strategy(working_dir, strategy_var, output_text, test_button)

    def ping():
        ping_host(host_entry, output_text)

    def toggle_autostart():
        nonlocal autostart_updating
        if autostart_updating:
            return
        autostart_updating = True
        try:
            if autostart_var.get():
                strategy = strategy_var.get()
                if not strategy or strategy == "Стратегии не найдены":
                    output_text.insert(END, "[ERROR] Не выбрана стратегия для автозапуска\n")
                    autostart_var.set(False)
                    return
                bat_path = os.path.join(working_dir, strategy)
                if not os.path.exists(bat_path):
                    output_text.insert(END, f"[ERROR] Файл {bat_path} не найден\n")
                    autostart_var.set(False)
                    return
                set_autostart(bat_path, True)
                output_text.insert(END, f"[INFO] Автозапуск включен для стратегии: {strategy}\n")
            else:
                set_autostart(None, False)
                output_text.insert(END, "[INFO] Автозапуск отключен\n")
            output_text.see(END)
        finally:
            autostart_updating = False

    def update_autostart_checkbox():
        nonlocal autostart_updating
        if autostart_updating:
            return
        autostart_updating = True
        try:
            if is_autostart_enabled():
                bat_in_task = get_autostart_bat_path()
                if bat_in_task:
                    current_strategy = strategy_var.get()
                    expected_path = os.path.join(working_dir, current_strategy)
                    if bat_in_task == expected_path:
                        autostart_var.set(True)
                        return
            autostart_var.set(False)
        finally:
            autostart_updating = False

    def on_strategy_change(*args):
        update_autostart_checkbox()

    def change_theme():
        current = theme_var.get()
        new_theme = "dark" if current == "light" else "light"
        theme_var.set(new_theme)
        apply_theme(root, output_text, new_theme)

    def start_service():
        run_service_bat(working_dir, output_text)

    strategy_var.trace('w', on_strategy_change)

    OptionMenu(root, strategy_var, strategies[0], *strategies[1:]).pack(pady=5)

    btn_frame = Frame(root)
    btn_frame.pack(pady=5)
    Button(btn_frame, text="Запустить обход", command=run_zapret, width=15).pack(side="left", padx=5)
    Button(btn_frame, text="Остановить обход", command=stop, width=15, bg="orange").pack(side="left", padx=5)
    test_button = Button(btn_frame, text="Проверить стратегию", command=test_strategy, width=16, bg="lightblue")
    test_button.pack(side="left", padx=5)
    Button(btn_frame, text="Запустить сервис", command=start_service, width=14, bg="lightgreen").pack(side="left", padx=5)
    Button(btn_frame, text="Сменить тему", command=change_theme, width=12, bg="gray").pack(side="left", padx=5)

    # Кликабельные ссылки (Telegram и GitHub)
    links_frame = Frame(root)
    links_frame.pack(pady=5)
    tg_label = Label(links_frame, text="✈️ Telegram канал", font=("Arial", 10, "underline"), fg="blue", cursor="hand2")
    tg_label.pack(side="left", padx=10)
    tg_label.bind("<Button-1>", lambda e: open_telegram_channel())
    gh_label = Label(links_frame, text="🐙 GitHub репозиторий", font=("Arial", 10, "underline"), fg="blue", cursor="hand2")
    gh_label.pack(side="left", padx=10)
    gh_label.bind("<Button-1>", lambda e: open_github())

    Checkbutton(root, text="Автозапуск выбранной стратегии при включении ПК",
                variable=autostart_var,
                command=toggle_autostart).pack(pady=5)

    Label(root, text="Проверка пинга (хост):").pack(pady=(10,0))
    host_frame = Frame(root)
    host_frame.pack(pady=5)
    host_entry = tk.StringVar()
    host_entry.set("discord.com")
    Entry(host_frame, textvariable=host_entry, width=30).pack(side="left", padx=5)
    Button(host_frame, text="Пинг", command=ping, width=10).pack(side="left")

    Label(root, text="Лог:").pack(pady=(10,0))
    text_frame = Frame(root)
    text_frame.pack(pady=5, fill="both", expand=True)
    scroll = Scrollbar(text_frame)
    output_text = Text(text_frame, wrap="word", yscrollcommand=scroll.set, height=14)
    scroll.config(command=output_text.yview)
    output_text.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    update_autostart_checkbox()
    apply_theme(root, output_text, "light")
    root.mainloop()

# ------------------------------------------------------------
# 5. Точка входа
# ------------------------------------------------------------
def main():
    if not is_admin():
        run_as_admin()
        return

    working_dir = get_working_dir()
    first_run = not os.path.exists(working_dir) or not os.listdir(working_dir)
    if first_run:
        extract_resources()
        patch_bat_files(working_dir)
        current_exe = sys.executable if getattr(sys, 'frozen', False) else __file__
        create_desktop_shortcut(current_exe)
        messagebox.showinfo("Установка завершена",
                            f"Файлы распакованы в папку:\n{working_dir}\n\nЯрлык создан на рабочем столе.")
    main_gui(working_dir)

if __name__ == "__main__":
    main()