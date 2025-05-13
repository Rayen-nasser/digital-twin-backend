import logging
import os
import aiohttp
import json
import time
import asyncio
from django.conf import settings

logger = logging.getLogger(__name__)


class SpeechToTextService:
    """
    Service for converting speech to text using AssemblyAI API
    """

    def __init__(self):
        # Set up API keys and endpoints
        self.api_key = getattr(settings, 'ASSEMBLY_AI_API_KEY', '')
        self.base_url = "https://api.assemblyai.com/v2"
        self.headers = {
            "authorization": self.api_key,
            "content-type": "application/json"
        }
        self.default_language = getattr(settings, 'SPEECH_TO_TEXT_DEFAULT_LANGUAGE', 'en')

        # Set up storage client if needed
        if hasattr(settings, 'CLOUD_STORAGE_CLIENT'):
            self.storage_client = settings.CLOUD_STORAGE_CLIENT
        else:
            self.storage_client = None

    async def transcribe_voice(self, storage_path, language_code=None):
        """
        Transcribe a voice recording from storage path using AssemblyAI
        """
        if not language_code:
            language_code = self.default_language

        if not self.api_key:
            logger.error("AssemblyAI API key not configured")
            return "Speech transcription service not properly configured."

        try:
            # Get the audio file content
            audio_content = await self._get_file_content(storage_path)
            if not audio_content:
                return "Could not access voice recording file."

            # Check file size - ensure it's not too small, empty, or too large (25MB limit for AssemblyAI)
            file_size = len(audio_content)
            if file_size == 0:
                return "Voice recording file is empty."
            elif file_size < 1000:  # Less than 1KB is almost certainly not a valid audio file
                logger.error(f"File too small: {file_size} bytes - minimum 1KB required for valid audio")
                return "Voice recording file is too small to be processed (minimum 1KB required)."
            elif file_size > 25 * 1024 * 1024:  # 25MB in bytes
                return "Voice recording file exceeds maximum size limit of 25MB."

            # Validate file format - check first few bytes for common audio signatures
            # This is basic validation and may need to be expanded for more formats
            if not self._is_valid_audio_file(audio_content):
                return "Invalid audio file format. Please use a supported audio format."

            # Upload the file to AssemblyAI with retry logic
            upload_url = await self._upload_file_with_retry(audio_content)
            if not upload_url:
                return "Failed to upload audio file for transcription."

            # Submit transcription request
            transcript_id = await self._submit_transcription(upload_url, language_code)
            if not transcript_id:
                return "Failed to initialize transcription."

            # Poll for results
            transcript = await self._poll_for_completion(transcript_id)

            return transcript if transcript else "No speech detected."

        except Exception as e:
            logger.error(f"Error during speech transcription: {str(e)}", exc_info=True)
            return "Sorry, I couldn't transcribe your voice message."

    def _is_valid_audio_file(self, content):
        """
        Basic validation of audio file format based on file signatures
        """
        if len(content) < 12:  # Need at least some bytes to check
            logger.error(f"File too small for format detection: {len(content)} bytes")
            return False

        # Check for common audio format signatures
        signatures = {
            b'RIFF': 'wav',        # WAV files
            b'ID3': 'mp3',         # MP3 files with ID3 tag
            b'\xFF\xFB': 'mp3',    # MP3 files without ID3
            b'OggS': 'ogg',        # OGG files
            b'fLaC': 'flac',       # FLAC files
            b'\x1A\x45\xDF\xA3': 'webm'  # WEBM files
        }

        # Check for known signatures
        for sig, format_type in signatures.items():
            if content.startswith(sig):
                logger.info(f"Detected audio format: {format_type}")
                return True

        # For MP3 without clear header, check for MP3 frame sync
        if content[0] == 0xFF and (content[1] & 0xE0) == 0xE0:
            logger.info("Detected audio format: mp3 (frame sync)")
            return True

        # For WEBM/matroska files with different starting signature
        if b'matroska' in content[:100] or b'webm' in content[:100]:
            logger.info("Detected audio format: webm/matroska")
            return True

        # Log the header for debugging
        logger.warning(f"Unknown audio format. File header (hex): {content[:12].hex()}")

        # File is likely too small or not a valid audio file
        if len(content) < 1000:
            logger.error("File too small to be valid audio")
            return False

        return False  # Return false if we can't identify the audio format

    async def _get_file_content(self, storage_path):
        """
        Get file content from storage path (cloud storage or local)
        """
        try:
            # Check if the path is a URL
            if storage_path.startswith('http://') or storage_path.startswith('https://'):
                async with aiohttp.ClientSession() as session:
                    async with session.get(storage_path) as response:
                        if response.status == 200:
                            return await response.read()
                        else:
                            logger.error(f"Failed to fetch voice file from URL: {storage_path}, status: {response.status}")
                            return None

            # Check if we have cloud storage client
            elif self.storage_client and (storage_path.startswith('gs://') or storage_path.startswith('s3://')):
                # Here you would implement cloud storage specific retrieval
                # This is placeholder logic - implement according to your storage provider
                if storage_path.startswith('gs://'):
                    # Google Cloud Storage example
                    bucket_name = storage_path.split('/')[2]
                    blob_name = '/'.join(storage_path.split('/')[3:])
                    bucket = self.storage_client.bucket(bucket_name)
                    blob = bucket.blob(blob_name)
                    return blob.download_as_bytes()
                elif storage_path.startswith('s3://'):
                    # AWS S3 example - would need boto3 implemented
                    logger.error("S3 retrieval not implemented")
                    return None

            # Local file
            else:
                # Check if the file exists
                if os.path.exists(storage_path):
                    with open(storage_path, 'rb') as file:
                        return file.read()
                else:
                    logger.error(f"Voice file not found at path: {storage_path}")
                    return None

        except Exception as e:
            logger.error(f"Error fetching voice file: {str(e)}", exc_info=True)
            return None

    async def _upload_file_with_retry(self, audio_content, max_retries=3):
        """
        Upload audio file to AssemblyAI with retry logic
        """
        retries = 0
        while retries < max_retries:
            try:
                upload_endpoint = f"{self.base_url}/upload"

                # Create a clean headers dict without content-type for upload
                upload_headers = {"authorization": self.headers["authorization"]}

                # Log attempt details
                logger.info(f"Attempting AssemblyAI upload (attempt {retries + 1}/{max_retries})")
                logger.info(f"Audio content size: {len(audio_content)} bytes")

                # Add a small delay between retries
                if retries > 0:
                    await asyncio.sleep(2)

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        upload_endpoint,
                        headers=upload_headers,
                        data=audio_content,
                        timeout=aiohttp.ClientTimeout(total=60)  # Increase timeout for large files
                    ) as response:
                        if response.status == 200:
                            response_json = await response.json()
                            logger.info("Upload successful")
                            return response_json.get("upload_url")
                        else:
                            error_text = await response.text()
                            logger.error(f"AssemblyAI upload failed (attempt {retries + 1}): {response.status}, {error_text}")

                            # Check for specific error conditions
                            if response.status == 401:
                                logger.error("Authorization failed - check API key")
                                return None  # Don't retry auth errors
                            elif response.status == 429:
                                # Rate limit - wait longer before retry
                                retry_after = int(response.headers.get('Retry-After', 5))
                                logger.info(f"Rate limited, waiting {retry_after} seconds")
                                await asyncio.sleep(retry_after)

                retries += 1

            except asyncio.TimeoutError:
                logger.error(f"Timeout during upload (attempt {retries + 1})")
                retries += 1

            except Exception as e:
                logger.error(f"Error uploading file to AssemblyAI (attempt {retries + 1}): {str(e)}", exc_info=True)
                retries += 1

        logger.error(f"Failed to upload after {max_retries} attempts")
        return None

    async def _submit_transcription(self, audio_url, language_code):
        """
        Submit transcription request to AssemblyAI
        """
        try:
            transcript_endpoint = f"{self.base_url}/transcript"

            data = {
                "audio_url": audio_url,
                "language_code": language_code,
                "speech_model": "universal"  # Changed from 'default' to 'universal' which is a valid option
            }

            # Log the transcription request
            logger.info(f"Submitting transcription request for: {audio_url}")
            logger.info(f"Language code: {language_code}")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    transcript_endpoint,
                    headers=self.headers,
                    json=data,
                    timeout=aiohttp.ClientTimeout(total=30)  # Set a reasonable timeout
                ) as response:
                    if response.status == 200:
                        response_json = await response.json()
                        transcript_id = response_json.get("id")
                        logger.info(f"Transcription request accepted, ID: {transcript_id}")
                        return transcript_id
                    else:
                        error_text = await response.text()
                        logger.error(f"AssemblyAI transcription request failed: {response.status}, {error_text}")
                        return None

        except Exception as e:
            logger.error(f"Error submitting transcription to AssemblyAI: {str(e)}", exc_info=True)
            return None

    async def _poll_for_completion(self, transcript_id):
        """
        Poll AssemblyAI API for transcription completion
        """
        try:
            polling_endpoint = f"{self.base_url}/transcript/{transcript_id}"

            # Maximum number of polling attempts (5 min timeout with 3s interval)
            max_attempts = 100
            attempts = 0

            logger.info(f"Polling for transcription completion, ID: {transcript_id}")

            async with aiohttp.ClientSession() as session:
                while attempts < max_attempts:
                    async with session.get(
                        polling_endpoint,
                        headers=self.headers
                    ) as response:
                        if response.status == 200:
                            response_json = await response.json()
                            status = response_json.get("status")

                            # Log progress occasionally
                            if attempts % 10 == 0:
                                logger.info(f"Transcription status after {attempts*3}s: {status}")

                            if status == "completed":
                                transcript = response_json.get("text", "")
                                logger.info(f"Transcription completed, length: {len(transcript)} chars")
                                return transcript
                            elif status == "error":
                                error_msg = response_json.get("error", "Unknown error")
                                logger.error(f"AssemblyAI transcription error: {error_msg}")
                                return f"Transcription error: {error_msg}"
                            else:
                                # Wait before next polling attempt
                                await asyncio.sleep(3)
                                attempts += 1
                        else:
                            error_text = await response.text()
                            logger.error(f"AssemblyAI polling failed: {response.status}, {error_text}")
                            return "Transcription service error."

            # If we reach here, we've timed out
            logger.error(f"Timeout waiting for AssemblyAI transcription: {transcript_id}")
            return "Transcription timed out."

        except Exception as e:
            logger.error(f"Error polling AssemblyAI: {str(e)}", exc_info=True)
            return "Error checking transcription status."