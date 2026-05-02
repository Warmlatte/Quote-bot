import json
import logging
import os
import re

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive"]
_FOLDER_RE = re.compile(r"drive\.google\.com/drive/folders/([^/?]+)")
_MODEL_EXTS = {".stl", ".obj"}
_FOLDER_MIME = "application/vnd.google-apps.folder"
_SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
_GOOGLE_APPS_MIME_PREFIX = "application/vnd.google-apps."


def extract_folder_id(url: str) -> str:
    m = _FOLDER_RE.search(url)
    if not m:
        raise ValueError(f"無效的 Google Drive 資料夾連結：{url!r}")
    return m.group(1)


class DriveClient:
    def __init__(self, service_account_json: str) -> None:
        info = json.loads(service_account_json)
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
        self._service = build("drive", "v3", credentials=creds)

    def list_model_files(self, folder_id: str) -> list[dict]:
        query = f"'{folder_id}' in parents and trashed = false"
        response = (
            self._service.files()
            .list(
                q=query,
                fields="files(id, name)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        return [
            f for f in response.get("files", [])
            if os.path.splitext(f["name"])[1].lower() in _MODEL_EXTS
        ]

    def list_model_files_recursive(
        self, folder_id: str, max_depth: int = 5
    ) -> list[dict]:
        return self._scan_folder(folder_id, 0, max_depth)

    def _scan_folder(
        self, folder_id: str, current_depth: int, max_depth: int
    ) -> list[dict]:
        query = f"'{folder_id}' in parents and trashed = false"
        results: list[dict] = []
        page_token: str | None = None

        while True:
            kwargs: dict = dict(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, shortcutDetails)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            if page_token:
                kwargs["pageToken"] = page_token
            response = self._service.files().list(**kwargs).execute()

            for item in response.get("files", []):
                if item["mimeType"] == _FOLDER_MIME:
                    if current_depth + 1 >= max_depth:
                        logger.warning(
                            "Skipping subfolder '%s' (id=%s): max_depth=%d reached",
                            item["name"],
                            item["id"],
                            max_depth,
                        )
                    else:
                        results.extend(
                            self._scan_folder(item["id"], current_depth + 1, max_depth)
                        )
                elif item["mimeType"] == _SHORTCUT_MIME:
                    details = item.get("shortcutDetails", {})
                    target_id = details.get("targetId")
                    target_mime = details.get("targetMimeType", "")
                    if (
                        target_id
                        and not target_mime.startswith(_GOOGLE_APPS_MIME_PREFIX)
                        and os.path.splitext(item["name"])[1].lower() in _MODEL_EXTS
                    ):
                        results.append({"id": target_id, "name": item["name"]})
                elif (
                    not item["mimeType"].startswith(_GOOGLE_APPS_MIME_PREFIX)
                    and os.path.splitext(item["name"])[1].lower() in _MODEL_EXTS
                ):
                    results.append({"id": item["id"], "name": item["name"]})

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return results

    def download_file(self, file_id: str, dest_path: str) -> str:
        request = self._service.files().get_media(
            fileId=file_id, supportsAllDrives=True
        )
        with open(dest_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return dest_path

    def rename_folder(self, folder_id: str, name: str) -> None:
        self._service.files().update(
            fileId=folder_id,
            body={"name": name},
            supportsAllDrives=True,
        ).execute()

    def upload_file(
        self,
        file_path: str,
        folder_id: str,
        mimetype: str = "application/pdf",
    ) -> str:
        file_name = os.path.basename(file_path)
        media = MediaFileUpload(file_path, mimetype=mimetype)
        metadata = {"name": file_name, "parents": [folder_id]}
        file_obj = (
            self._service.files()
            .create(
                body=metadata,
                media_body=media,
                fields="id,webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        file_id = file_obj["id"]
        self._service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            supportsAllDrives=True,
        ).execute()
        return file_obj["webViewLink"]
