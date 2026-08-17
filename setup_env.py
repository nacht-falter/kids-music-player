import base64
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Tuple

import requests


class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'


def print_header(text: str):
    """Print a styled header"""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}{Colors.END}")


def print_step(step_num: int, total_steps: int, description: str):
    """Print a step indicator"""
    print(
        f"\n{Colors.CYAN}{Colors.BOLD}[Step {step_num}/{total_steps}] {description}{Colors.END}")


def print_success(message: str):
    """Print a success message"""
    print(f"{Colors.GREEN}✓ {message}{Colors.END}")


def print_warning(message: str):
    """Print a warning message"""
    print(f"{Colors.YELLOW}⚠ {message}{Colors.END}")


def print_error(message: str):
    """Print an error message"""
    print(f"{Colors.RED}✗ {message}{Colors.END}")


def print_info(message: str):
    """Print an info message"""
    print(f"{Colors.BLUE}ℹ {message}{Colors.END}")


def get_user_input(prompt: str, default: str = "", required: bool = True) -> str:
    """Get user input with optional default value"""
    if default:
        full_prompt = f"{Colors.BOLD}{prompt} [{default}]:{Colors.END} "
    else:
        full_prompt = f"{Colors.BOLD}{prompt}:{Colors.END} "

    while True:
        user_input = input(full_prompt).strip()

        if user_input:
            return user_input
        elif default:
            return default
        elif not required:
            return ""
        else:
            print_warning("This field is required. Please enter a value.")


def get_yes_no(prompt: str, default: bool = False) -> bool:
    """Get yes/no input from user"""
    default_str = "Y/n" if default else "y/N"
    full_prompt = f"{Colors.BOLD}{prompt} ({default_str}):{Colors.END} "

    while True:
        response = input(full_prompt).strip().lower()

        if response in ['y', 'yes']:
            return True
        elif response in ['n', 'no']:
            return False
        elif response == "":
            return default
        else:
            print_warning("Please enter 'y' for yes or 'n' for no.")


def validate_app_name(name: str) -> bool:
    """Validate app name for use in filenames and service names"""
    if not name:
        return False

    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return False

    if not (name[0].isalpha() or name[0] == '_'):
        return False

    return True


def get_app_name() -> str:
    """Get and validate app name from user"""
    print_info("Choose a name for your application. This will be used for:")
    print("  • Service name (systemd)")
    print("  • Configuration files")
    print("  • Process identification")
    print_info(
        "Name must contain only letters, numbers, underscores, and hyphens.")
    print_info("It must start with a letter or underscore.")

    while True:
        app_name = get_user_input(
            "Enter your app name",
            default="rfid_music_player"
        )

        if validate_app_name(app_name):
            print_success(f"App name '{app_name}' is valid!")
            return app_name
        else:
            print_error(
                "Invalid app name. Please follow the naming rules above.")


def write_systemd_service(app_name: str):
    """Create and install systemd service"""
    print_info("Creating systemd service configuration...")

    current_dir = os.getcwd()
    current_user = os.getenv('USER', 'pi')
    main_py_path = os.path.join(current_dir, 'main.py')

    print_info(f"Service will be configured for:")
    print(f"  • App name: {app_name}")
    print(f"  • Working directory: {current_dir}")
    print(f"  • Main script: {main_py_path}")
    print(f"  • User: {current_user}")

    if not os.path.exists(main_py_path):
        print_warning(f"main.py not found at {main_py_path}")
        if get_yes_no("Continue with service creation anyway?", default=True):
            print_info(
                "Service will be created but may need manual adjustment.")
        else:
            print_info("Skipping service creation.")
            return

    service_description = f"{app_name.replace('_', ' ').replace('-', ' ').title()}"

    service_content = f"""[Unit]
Description={service_description}
After=sound.target
Wants=sound.target

[Service]
ExecStart={current_dir}/venv/bin/python {main_py_path}
WorkingDirectory={current_dir}
StandardOutput=journal
StandardError=journal
Restart=on-failure
User=pi

[Install]
WantedBy=default.target
"""

    service_filename = f"{app_name.lower()}.service"
    service_path = f"/etc/systemd/system/{service_filename}"
    temp_file = f"{service_filename}.temp"

    try:
        with open(temp_file, "w") as f:
            f.write(service_content)

        print_success(f"Service file created as {temp_file}")

        if get_yes_no("Would you like to install the systemd service now?", default=False):
            print_warning(
                "Installing systemd service requires sudo privileges...")

            try:
                subprocess.run(
                    ["sudo", "mv", temp_file, service_path], check=True)
                subprocess.run(
                    ["sudo", "systemctl", "daemon-reload"], check=True)
                subprocess.run(["sudo", "systemctl", "enable",
                               service_filename], check=True)

                print_success("Systemd service installed and enabled!")
                print_info(
                    f"You can now start the service with: sudo systemctl start {app_name.lower()}")

            except subprocess.CalledProcessError as e:
                print_error(f"Failed to install systemd service: {e}")
                print_info(f"You can manually install later with:")
                print(f"  sudo mv {temp_file} {service_path}")
                print(f"  sudo systemctl daemon-reload")
                print(f"  sudo systemctl enable {service_filename}")
        else:
            print_info("Service file created but not installed.")
            print_info(f"To install later, run:")
            print(f"  sudo mv {temp_file} {service_path}")
            print(f"  sudo systemctl daemon-reload")
            print(f"  sudo systemctl enable {service_filename}")

    except Exception as e:
        print_error(f"Failed to create systemd service: {e}")
        raise


def install_dependencies():
    """Create a virtual environment and install Python dependencies"""
    print_info("Creating virtual environment...")

    venv_path = os.path.join(os.getcwd(), "venv")
    python_bin = os.path.join(venv_path, "bin", "python")
    pip_bin = os.path.join(venv_path, "bin", "pip")

    # Create venv if it doesn't exist
    if not os.path.isdir(venv_path):
        subprocess.run([sys.executable, "-m", "venv", venv_path], check=True)
        print_success("Virtual environment created.")
    else:
        print_info("Virtual environment already exists.")

    print_info("Installing Python dependencies from requirements.txt...")

    try:
        subprocess.run(
            [pip_bin, "install", "--upgrade", "-r", "requirements.txt"],
            check=True,
            capture_output=True,
            text=True
        )
        print_success("Dependencies installed successfully!")
    except subprocess.CalledProcessError as e:
        print_error(f"Failed to install dependencies:\n{e.stderr}")


def restart_in_venv():
    venv_python = os.path.join(os.getcwd(), "venv", "bin", "python")
    if not os.path.exists(venv_python):
        print_error("Virtual environment Python binary not found.")
        sys.exit(1)

    if sys.executable != venv_python:
        print_info("Restarting script using virtual environment...")
        os.execv(venv_python, [venv_python] + sys.argv)

def find_rfid_device(device_name_substring: str = "RFID") -> str:
    """Find and return RFID device name"""
    print_info("Scanning for RFID devices...")

    try:
        devices_found = []
        device_name_map = {}

        for path in list_devices():
            dev = InputDevice(path)
            devices_found.append(f"  • {dev.name} ({dev.path})")
            device_name_map[dev.name] = path

            if device_name_substring.upper() in dev.name.upper():
                print_success(f"Found RFID device: {dev.name}")
                return dev.name

        print_warning("RFID device not found automatically.")
        print_info("Available input devices:")
        for device in devices_found:
            print(device)

        print_info(f"Looking for devices containing '{device_name_substring}'")

        if get_yes_no("Would you like to manually specify the device name?"):
            while True:
                manual_name = get_user_input("Enter the exact device name")
                for name in device_name_map:
                    if manual_name.lower() == name.lower():
                        print_success(f"Manually selected RFID device: {name}")
                        return name
                print_error("Device name not found. Please try again.")
        else:
            raise RuntimeError(
                "RFID device not found and no manual override provided.")

    except Exception as e:
        print_error(f"Error scanning for devices: {e}")
        raise


def setup_spotify() -> Tuple[str, str, str]:
    """Get Spotify configuration from user"""
    print_info("Setting up Spotify integration...")
    print_info(
        "You'll need your Spotify client ID, client secret, refresh token, and device ID.")
    print_info(
        "Visit the Spotify Developer Dashboard to obtain these credentials.")

    client_id = get_user_input(
        "Enter your Spotify client ID",
        required=True
    )
    client_secret = get_user_input(
        "Enter your Spotify client secret",
        required=True
    )
    refresh_token = get_user_input(
        "Enter your Spotify refresh token",
        required=True
    )
    device_id = get_user_input(
        "Enter your Spotify device ID",
        required=True
    )

    # Encode the client credentials
    usercreds = encode_spotify_credentials(client_id, client_secret)

    is_valid, error_message = test_spotify_credentials(
        refresh_token, usercreds, device_id)
    if not is_valid:
        print_error(f"Spotify setup failed: {error_message}")
        if get_yes_no("Would you like to continue setup without Spotify for now?", default=False):
            print_warning(
                "Continuing without Spotify setup - you'll need to set it up manually.")
            refresh_token = usercreds = device_id = ""
        else:
            print_info("Please correct the issues and run setup again.")
            sys.exit(1)
    else:
        print_success("Spotify setup successful!")
    return refresh_token, usercreds, device_id


def encode_spotify_credentials(client_id: str, client_secret: str) -> str:
    """Encode client ID and secret to base64 for Spotify API authentication"""
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(
        credentials.encode('utf-8')).decode('utf-8')
    print(encoded_credentials)
    return encoded_credentials


def test_spotify_credentials(refresh_token: str, usercreds: str, device_id: str) -> Tuple[bool, str]:
    """Test if the Spotify credentials, refresh token, and device ID are valid."""
    # First, test the credentials and refresh token
    headers = {
        "Authorization": f"Basic {usercreds}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    response = requests.post(
        "https://accounts.spotify.com/api/token", headers=headers, data=data)
    if response.status_code != 200:
        return False, "Invalid credentials or refresh token. Please check your Spotify client ID, client secret, and refresh token."
    # If credentials are valid, test the device ID
    access_token = response.json().get('access_token')
    if not access_token:
        return False, "Failed to obtain access token. Please try again."
    is_device_valid, device_error_message = check_spotify_device(
        access_token, device_id)
    if not is_device_valid:
        return False, device_error_message
    return True, "All checks passed."


def check_spotify_device(access_token: str, device_id: str) -> Tuple[bool, str]:
    """Check if the Spotify device is available."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    response = requests.get(
        "https://api.spotify.com/v1/me/player/devices", headers=headers)
    if response.status_code != 200:
        return False, "Failed to fetch device list. Please check your internet connection and try again."
    devices = response.json().get('devices', [])
    for device in devices:
        if device['id'] == device_id:
            return True, "Device is available."
    return False, f"Device with ID '{device_id}' is not available. Please check your device ID and ensure the device is active."


def setup_sync() -> Tuple[bool, str, str]:
    """Configure sync settings"""
    print_info("Sync allows you to register RFID codes through an external API.")

    enable_sync = get_yes_no(
        "Do you want to enable sync functionality?", default=False)

    sync_api_url = ""
    sync_api_token = ""

    if enable_sync:
        print_info("Configuring sync settings...")

        sync_api_url = get_user_input(
            "Enter the sync API URL",
            required=True
        )

        sync_api_token = get_user_input(
            "Enter the sync API token",
            required=True
        )

        if not test_sync_api(sync_api_url, sync_api_token):
            print_error(
                "Sync API validation failed. Please check the URL and token.")
            if get_yes_no("Would you like to continue setup without sync for now?", default=False):
                print_warning(
                    "Continuing without sync - you can still set it up manually later.")
                sync_api_url = sync_api_token = ""
                enable_sync = False
            else:
                print_info("")
                sys.exit(1)
        else:
            print_success("Sync configuration completed!")
    else:
        print_info("Sync functionality disabled.")

    return enable_sync, sync_api_url, sync_api_token


def test_sync_api(api_url: str, token: str) -> bool:
    """Test if the sync API is reachable and the token is valid."""
    try:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(f"{api_url}/music", headers=headers, timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False


def write_env_file(app_name: str, rfid_device_name: str, spotify_refresh_token: str,
                   spotify_device_id: str, spotify_user_id: str, enable_sync: bool,
                   sync_api_url: str, sync_api_token: str):
    """Write environment configuration file"""

    def generate_content():
        return f"""\
APP_NAME={app_name}
SPOTIFY_USERCREDS={spotify_user_id}
SPOTIFY_REFRESH_TOKEN={spotify_refresh_token}
SPOTIFY_DEVICE_ID={spotify_device_id}
DATABASE_URL={app_name.lower()}.db
# DEVELOPMENT=false
# IDLE_TIME=3600
RFID_READER={rfid_device_name}
ENABLE_SYNC={'true' if enable_sync else ''}
SYNC_API_URL={sync_api_url}
SYNC_API_TOKEN={sync_api_token}
"""

    env_file = ".env"
    content = generate_content()

    print_info("Creating environment configuration file...")

    if os.path.exists(env_file):
        print_warning(f"Configuration file '{env_file}' already exists!")

        try:
            stat_info = os.stat(env_file)
            mod_time = datetime.fromtimestamp(
                stat_info.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            print_info(
                f"Modified: {mod_time} — Size: {stat_info.st_size} bytes")
        except Exception:
            pass

        print_info("Choose what to do:")
        print("  1. Overwrite existing file (backup will be created)")
        print("  2. Skip .env creation")
        print("  3. Append new entries to existing .env")
        print("  4. Write to a different filename")

        while True:
            choice = get_user_input("Enter your choice (1–4)", required=True)

            if choice == "1":
                backup_file = f"{env_file}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                try:
                    shutil.copy2(env_file, backup_file)
                    print_success(f"Backup created: {backup_file}")
                except Exception as e:
                    print_error(f"Backup failed: {e}")
                    if not get_yes_no("Continue without backup?", default=False):
                        print_info("Aborting .env update.")
                        return
                break

            elif choice == "2":
                print_info("Skipping .env creation.")
                return

            elif choice == "3":
                try:
                    with open(env_file, "a") as f:
                        f.write(
                            f"\n# New entries added {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                        f.write(content)
                    print_success("New entries appended to .env.")
                except Exception as e:
                    print_error(f"Failed to append: {e}")
                    raise
                return

            elif choice == "4":
                alt_filename = get_user_input(
                    "Enter new filename for environment config",
                    default=f"{app_name.lower()}.env"
                )
                try:
                    with open(alt_filename, "w") as f:
                        f.write(content)
                    print_success(
                        f"Environment configuration written to {alt_filename}")
                except Exception as e:
                    print_error(f"Failed to write to {alt_filename}: {e}")
                    raise
                return

            else:
                print_warning("Invalid choice. Please enter 1–4.")

    else:
        try:
            with open(env_file, "w") as f:
                f.write(content)
            print_success(".env created successfully.")
        except Exception as e:
            print_error(f"Failed to create .env: {e}")
            raise


def check_mpd_mpc():
    """Check for MPD and MPC installation"""
    print_info("Checking for MPD (Music Player Daemon) and MPC (client)...")

    def is_installed(cmd: str) -> bool:
        return shutil.which(cmd) is not None

    if not is_installed("mpd"):
        print_error("MPD (Music Player Daemon) not found!")
        print_info("To install MPD, run: sudo apt update && sudo apt install mpd")

        if get_yes_no("Would you like to continue setup without MPD for now?", default=False):
            print_warning(
                "Continuing without MPD - you'll need to install it later.")
        else:
            print_info("Please install MPD and run setup again.")
            sys.exit(1)
    else:
        print_success("MPD found!")

    if not is_installed("mpc"):
        print_error("MPC (MPD client) not found!")
        print_info("To install MPC, run: sudo apt update && sudo apt install mpc")

        if get_yes_no("Would you like to continue setup without MPC for now?", default=False):
            print_warning(
                "Continuing without MPC - you'll need to install it later.")
            return
        else:
            print_info("Please install MPC and run setup again.")
            sys.exit(1)
    else:
        print_success("MPC found!")

    if is_installed("mpc"):
        try:
            subprocess.run(
                ["mpc", "status"],
                check=True,
                capture_output=True,
                text=True
            )
            print_success("MPC successfully connected to MPD!")

        except subprocess.CalledProcessError as e:
            print_warning(f"MPC could not connect to MPD. Error: {e}")
            print_info("This might be normal if MPD isn't running yet.")
            print_info("Common solutions:")
            print("  • Start MPD: sudo systemctl start mpd")
            print("  • Enable MPD: sudo systemctl enable mpd")
            print("  • Check MPD config: sudo nano /etc/mpd.conf")

            if not get_yes_no("Continue with setup anyway?", default=True):
                print_info(
                    "Setup cancelled. Please configure MPD and try again.")
                sys.exit(1)
    else:
        print_info("Skipping MPD connection test (MPC not available).")


def main():
    """Main setup function"""
    print_header("RFID Music Player Setup")
    print_info("This script will help you configure your RFID Music Player.")
    print_info(
        "Please ensure you have the necessary permissions and credentials ready.")

    if not get_yes_no("Ready to begin setup?", default=True):
        print_info("Setup cancelled by user.")
        return

    total_steps = 7
    current_step = 1

    try:
        print_step(current_step, total_steps, "Setting Application Name")
        app_name = get_app_name()
        current_step += 1

        print_step(current_step, total_steps, "Installing Dependencies")
        install_dependencies()
        restart_in_venv()
        current_step += 1

        global requests
        import requests

        global InputDevice, list_devices
        from evdev import InputDevice, list_devices

        print_step(current_step, total_steps, "Detecting RFID Device")
        rfid_device_name = find_rfid_device()
        current_step += 1

        print_step(current_step, total_steps, "Checking MPD/MPC")
        check_mpd_mpc()
        current_step += 1

        print_step(current_step, total_steps, "Configuring Spotify")
        spotify_refresh_token, spotify_usercreds, spotify_device_id = setup_spotify()
        current_step += 1

        print_step(current_step, total_steps, "Configuring Sync Settings")
        enable_sync, sync_api_url, sync_api_token = setup_sync()
        current_step += 1

        print_step(current_step, total_steps, "Creating Configuration Files")
        write_env_file(app_name, rfid_device_name, spotify_refresh_token, spotify_usercreds,
                       spotify_device_id, enable_sync, sync_api_url, sync_api_token)
        write_systemd_service(app_name)

        print_header(f"{app_name} Setup Complete!")
        print_success(f"Your {app_name} has been configured successfully!")

    except KeyboardInterrupt:
        print_warning("\nSetup interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print_error(f"Setup failed: {e}")
        print_info("Please check the error above and try again.")
        sys.exit(1)


if __name__ == "__main__":
    main()
