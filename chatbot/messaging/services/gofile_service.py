import os
import logging
import requests
import time
from urllib.parse import urljoin
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

class GoFileUploader:
    """Service for uploading files to GoFile"""

    BASE_URL = "https://api.gofile.io/"

    # Fallback options if the primary method fails
    ALTERNATIVE_SERVERS = ["store1", "store2", "store3", "store4", "store5"]
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # seconds
    TIMEOUT_CONNECT = 10  # connection timeout in seconds
    TIMEOUT_READ = 30    # read timeout in seconds

    @staticmethod
    def get_server():
        """Get the best server for uploading with retries"""
        for attempt in range(GoFileUploader.MAX_RETRIES):
            try:
                logger.info("Requesting best GoFile server")
                response = requests.get(
                    urljoin(GoFileUploader.BASE_URL, "getServer"),
                    timeout=(GoFileUploader.TIMEOUT_CONNECT, GoFileUploader.TIMEOUT_READ)
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "ok":
                        server = data.get("data", {}).get("server")
                        logger.info(f"Successfully got GoFile server: {server}")
                        return server
                    else:
                        logger.warning(f"GoFile server selection failed: {data.get('status')} - {data.get('message', '')}")
                else:
                    logger.warning(f"GoFile server selection failed: HTTP {response.status_code} - {response.text}")

                # Wait before retry
                if attempt < GoFileUploader.MAX_RETRIES - 1:
                    logger.info(f"Retrying GoFile server selection in {GoFileUploader.RETRY_DELAY} seconds (attempt {attempt+1}/{GoFileUploader.MAX_RETRIES})")
                    time.sleep(GoFileUploader.RETRY_DELAY)
            except requests.exceptions.Timeout:
                logger.error("GoFile server request timed out")
                if attempt < GoFileUploader.MAX_RETRIES - 1:
                    logger.info(f"Retrying GoFile server selection in {GoFileUploader.RETRY_DELAY} seconds (attempt {attempt+1}/{GoFileUploader.MAX_RETRIES})")
                    time.sleep(GoFileUploader.RETRY_DELAY)
            except requests.exceptions.RequestException as e:
                logger.error(f"Network error getting GoFile server: {str(e)}", exc_info=True)
                if attempt < GoFileUploader.MAX_RETRIES - 1:
                    logger.info(f"Retrying GoFile server selection in {GoFileUploader.RETRY_DELAY} seconds (attempt {attempt+1}/{GoFileUploader.MAX_RETRIES})")
                    time.sleep(GoFileUploader.RETRY_DELAY)
            except Exception as e:
                logger.error(f"Error getting GoFile server: {str(e)}", exc_info=True)
                if attempt < GoFileUploader.MAX_RETRIES - 1:
                    logger.info(f"Retrying GoFile server selection in {GoFileUploader.RETRY_DELAY} seconds (attempt {attempt+1}/{GoFileUploader.MAX_RETRIES})")
                    time.sleep(GoFileUploader.RETRY_DELAY)

        # If all attempts fail, try alternative servers
        logger.warning("All attempts to get GoFile server failed, using fallback server")
        # Return a default fallback server
        return GoFileUploader.ALTERNATIVE_SERVERS[0]

    @staticmethod
    def validate_file(file_path):
        """Validate file exists and is not empty"""
        if not os.path.exists(file_path):
            logger.error(f"File not found for upload: {file_path}")
            return False, "File not found"

        file_size = os.path.getsize(file_path)
        if file_size == 0:
            logger.error(f"File is empty: {file_path}")
            return False, "File is empty"

        # Check if file is readable
        try:
            with open(file_path, 'rb') as f:
                # Read a small chunk to test file access
                f.read(1)
        except IOError as e:
            logger.error(f"File is not accessible: {str(e)}")
            return False, f"File is not accessible: {str(e)}"

        # Get file extension to validate supported types
        _, ext = os.path.splitext(file_path)
        ext = ext.lower().replace('.', '')

        # Supported audio formats
        supported_formats = ['webm', 'mp3', 'wav', 'ogg', 'm4a', 'mp4', 'aac', 'flac']
        if ext not in supported_formats:
            logger.warning(f"File extension '{ext}' may not be supported")
            # Don't fail here, just warn

        return True, file_size

    @staticmethod
    def upload_file(file_path):
        """Upload a file to GoFile and return the download URL"""
        # Validate file first
        is_valid, validation_result = GoFileUploader.validate_file(file_path)
        if not is_valid:
            return None, None

        file_size = validation_result
        logger.info(f"Uploading file to GoFile: {file_path} (size: {file_size} bytes)")

        # Try each server in our list until one works
        servers_to_try = []

        # First, try to get the recommended server
        main_server = GoFileUploader.get_server()
        if main_server:
            servers_to_try.append(main_server)

        # Add fallback servers
        servers_to_try.extend(GoFileUploader.ALTERNATIVE_SERVERS)

        # Remove duplicates while preserving order
        servers_to_try = list(dict.fromkeys(servers_to_try))

        token = os.getenv('GOFILE_TOKEN')
        if not token:
            logger.warning("No GoFile token provided, upload will be anonymous")

        # Try each server
        for server in servers_to_try:
            try:
                upload_url = f"https://{server}.gofile.io/uploadFile"
                logger.info(f"Attempting upload to: {upload_url}")

                with open(file_path, 'rb') as file:
                    files = {'file': file}
                    data = {}
                    if token:
                        data['token'] = token

                    logger.info(f"Sending file upload request to {upload_url}")
                    response = requests.post(
                        upload_url,
                        files=files,
                        data=data,
                        timeout=(GoFileUploader.TIMEOUT_CONNECT, GoFileUploader.TIMEOUT_READ * 3)  # Longer timeout for uploads
                    )

                    if response.status_code == 200:
                        result = response.json()
                        logger.info(f"Upload response received: {result.get('status')}")

                        if result.get("status") == "ok":
                            file_data = result.get("data", {})
                            download_page = file_data.get("downloadPage")
                            direct_link = file_data.get("directLink")

                            if download_page and direct_link:
                                logger.info(f"Upload successful: {download_page}")
                                return download_page, direct_link
                            else:
                                logger.warning(f"Missing download links in response: {file_data}")
                        else:
                            logger.warning(f"GoFile upload failed on server {server}: {result}")
                    else:
                        logger.warning(f"GoFile upload failed on server {server}: HTTP {response.status_code} - {response.text}")

            except requests.exceptions.Timeout:
                logger.error(f"Request timed out when uploading to GoFile server {server}")
            except requests.exceptions.RequestException as e:
                logger.warning(f"Network error uploading to GoFile server {server}: {str(e)}")
            except Exception as e:
                logger.warning(f"Error uploading to GoFile server {server}: {str(e)}", exc_info=True)

        # If we've tried all servers and none worked
        logger.error("All GoFile upload attempts failed")
        return None, None