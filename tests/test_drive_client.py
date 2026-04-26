import io
import json
import os
from unittest.mock import MagicMock, call, patch

import pytest

SERVICE_ACCOUNT_JSON = json.dumps({"type": "service_account", "project_id": "test"})


class TestExtractFolderId:
    def test_plain_folder_url(self):
        from bot.drive.client import extract_folder_id
        url = "https://drive.google.com/drive/folders/1ABC123xyz"
        assert extract_folder_id(url) == "1ABC123xyz"

    def test_folder_url_with_usp(self):
        from bot.drive.client import extract_folder_id
        url = "https://drive.google.com/drive/folders/1ABC123xyz?usp=sharing"
        assert extract_folder_id(url) == "1ABC123xyz"

    def test_invalid_url_raises_value_error(self):
        from bot.drive.client import extract_folder_id
        with pytest.raises(ValueError, match="無效"):
            extract_folder_id("https://www.google.com/")

    def test_empty_string_raises_value_error(self):
        from bot.drive.client import extract_folder_id
        with pytest.raises(ValueError):
            extract_folder_id("")


class TestListModelFiles:
    def _make_client(self):
        from bot.drive.client import DriveClient
        with patch("bot.drive.client.Credentials.from_service_account_info"), \
             patch("bot.drive.client.build") as mock_build:
            mock_build.return_value = MagicMock()
            return DriveClient(SERVICE_ACCOUNT_JSON), mock_build.return_value

    def test_filters_stl_and_obj(self):
        from bot.drive.client import DriveClient
        with patch("bot.drive.client.Credentials.from_service_account_info"), \
             patch("bot.drive.client.build") as mock_build:
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            mock_service.files.return_value.list.return_value.execute.return_value = {
                "files": [
                    {"id": "1", "name": "model.stl"},
                    {"id": "2", "name": "figure.OBJ"},
                    {"id": "3", "name": "readme.txt"},
                    {"id": "4", "name": "part.STL"},
                ]
            }
            client = DriveClient(SERVICE_ACCOUNT_JSON)
            result = client.list_model_files("folder123")

        assert len(result) == 3
        names = [f["name"] for f in result]
        assert "model.stl" in names
        assert "figure.OBJ" in names
        assert "part.STL" in names
        assert "readme.txt" not in names

    def test_returns_id_and_name(self):
        from bot.drive.client import DriveClient
        with patch("bot.drive.client.Credentials.from_service_account_info"), \
             patch("bot.drive.client.build") as mock_build:
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            mock_service.files.return_value.list.return_value.execute.return_value = {
                "files": [{"id": "abc", "name": "model.stl"}]
            }
            client = DriveClient(SERVICE_ACCOUNT_JSON)
            result = client.list_model_files("folder123")

        assert result[0] == {"id": "abc", "name": "model.stl"}

    def test_empty_folder(self):
        from bot.drive.client import DriveClient
        with patch("bot.drive.client.Credentials.from_service_account_info"), \
             patch("bot.drive.client.build") as mock_build:
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            mock_service.files.return_value.list.return_value.execute.return_value = {"files": []}
            client = DriveClient(SERVICE_ACCOUNT_JSON)
            result = client.list_model_files("folder123")

        assert result == []


class TestDownloadFile:
    def test_returns_dest_path(self, tmp_path):
        from bot.drive.client import DriveClient
        with patch("bot.drive.client.Credentials.from_service_account_info"), \
             patch("bot.drive.client.build") as mock_build, \
             patch("bot.drive.client.MediaIoBaseDownload") as mock_dl_cls:
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            mock_downloader = MagicMock()
            mock_downloader.next_chunk.return_value = (MagicMock(progress=lambda: 1.0), True)
            mock_dl_cls.return_value = mock_downloader
            client = DriveClient(SERVICE_ACCOUNT_JSON)
            dest = str(tmp_path / "model.stl")
            result = client.download_file("file_id_123", dest)

        assert result == dest


class TestUploadFile:
    def test_returns_web_view_link(self, tmp_path):
        from bot.drive.client import DriveClient

        dummy_pdf = tmp_path / "quote.pdf"
        dummy_pdf.write_bytes(b"%PDF-dummy")

        with patch("bot.drive.client.Credentials.from_service_account_info"), \
             patch("bot.drive.client.build") as mock_build, \
             patch("bot.drive.client.MediaFileUpload"):
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            file_id = "uploaded_file_id"
            mock_service.files.return_value.create.return_value.execute.return_value = {
                "id": file_id,
                "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
            }
            client = DriveClient(SERVICE_ACCOUNT_JSON)
            result = client.upload_file(str(dummy_pdf), "folder_id_abc")

        assert result == f"https://drive.google.com/file/d/{file_id}/view"

    def test_sets_public_permission(self, tmp_path):
        from bot.drive.client import DriveClient

        dummy_pdf = tmp_path / "quote.pdf"
        dummy_pdf.write_bytes(b"%PDF-dummy")

        with patch("bot.drive.client.Credentials.from_service_account_info"), \
             patch("bot.drive.client.build") as mock_build, \
             patch("bot.drive.client.MediaFileUpload"):
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            mock_service.files.return_value.create.return_value.execute.return_value = {
                "id": "fid",
                "webViewLink": "https://drive.google.com/file/d/fid/view",
            }
            client = DriveClient(SERVICE_ACCOUNT_JSON)
            client.upload_file(str(dummy_pdf), "folder_id_abc")

        perm_call = mock_service.permissions.return_value.create.call_args
        body = perm_call[1]["body"] if perm_call[1] else perm_call[0][1]
        assert body["type"] == "anyone"
        assert body["role"] == "reader"
