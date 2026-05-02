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


class TestRenameFolder:
    def test_calls_files_update_with_name(self):
        from bot.drive.client import DriveClient
        with patch("bot.drive.client.Credentials.from_service_account_info"), \
             patch("bot.drive.client.build") as mock_build:
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            client = DriveClient(SERVICE_ACCOUNT_JSON)
            client.rename_folder("folder_abc", "王小明")

        mock_service.files.return_value.update.assert_called_once_with(
            fileId="folder_abc",
            body={"name": "王小明"},
            supportsAllDrives=True,
        )
        mock_service.files.return_value.update.return_value.execute.assert_called_once()


class TestListModelFilesRecursive:
    def _make_service_with_folder_responses(self, folder_responses: dict):
        mock_service = MagicMock()

        def list_side_effect(**kwargs):
            q = kwargs.get("q", "")
            folder_id = q.split("'")[1] if "'" in q else ""
            result_mock = MagicMock()
            result_mock.execute.return_value = {"files": folder_responses.get(folder_id, [])}
            return result_mock

        mock_service.files.return_value.list.side_effect = list_side_effect
        return mock_service

    def _run(self, folder_responses, folder_id="parent", **kwargs):
        from bot.drive.client import DriveClient
        mock_service = self._make_service_with_folder_responses(folder_responses)
        with patch("bot.drive.client.Credentials.from_service_account_info"), \
             patch("bot.drive.client.build") as mock_build:
            mock_build.return_value = mock_service
            client = DriveClient(SERVICE_ACCOUNT_JSON)
            return client.list_model_files_recursive(folder_id, **kwargs)

    def test_flat_folder_returns_model_files(self):
        folder_responses = {
            "parent": [
                {"id": "1", "name": "a.stl", "mimeType": "application/octet-stream"},
                {"id": "2", "name": "b.obj", "mimeType": "application/octet-stream"},
                {"id": "3", "name": "readme.txt", "mimeType": "text/plain"},
            ]
        }
        result = self._run(folder_responses)
        assert len(result) == 2
        names = {f["name"] for f in result}
        assert names == {"a.stl", "b.obj"}

    def test_one_level_of_subfolders(self):
        # Spec example: parent has subfolder-A (a.stl, b.stl), subfolder-B (c.stl, d.stl)
        folder_responses = {
            "parent": [
                {"id": "sf-a", "name": "subfolder-A", "mimeType": "application/vnd.google-apps.folder"},
                {"id": "sf-b", "name": "subfolder-B", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "sf-a": [
                {"id": "1", "name": "a.stl", "mimeType": "application/octet-stream"},
                {"id": "2", "name": "b.stl", "mimeType": "application/octet-stream"},
            ],
            "sf-b": [
                {"id": "3", "name": "c.stl", "mimeType": "application/octet-stream"},
                {"id": "4", "name": "d.stl", "mimeType": "application/octet-stream"},
            ],
        }
        result = self._run(folder_responses, max_depth=2)
        assert len(result) == 4
        names = {f["name"] for f in result}
        assert names == {"a.stl", "b.stl", "c.stl", "d.stl"}

    def test_mixed_files_and_subfolders_at_root(self):
        # Spec example: parent has root.stl, subfolder-A (a.stl), subfolder-B (b.stl)
        folder_responses = {
            "parent": [
                {"id": "1", "name": "root.stl", "mimeType": "application/octet-stream"},
                {"id": "sf-a", "name": "subfolder-A", "mimeType": "application/vnd.google-apps.folder"},
                {"id": "sf-b", "name": "subfolder-B", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "sf-a": [{"id": "2", "name": "a.stl", "mimeType": "application/octet-stream"}],
            "sf-b": [{"id": "3", "name": "b.stl", "mimeType": "application/octet-stream"}],
        }
        result = self._run(folder_responses, max_depth=2)
        assert len(result) == 3
        names = {f["name"] for f in result}
        assert names == {"root.stl", "a.stl", "b.stl"}

    def test_non_model_files_excluded_at_all_levels(self):
        folder_responses = {
            "parent": [
                {"id": "sf-a", "name": "subfolder-A", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "sf-a": [
                {"id": "1", "name": "model.stl", "mimeType": "application/octet-stream"},
                {"id": "2", "name": "readme.txt", "mimeType": "text/plain"},
                {"id": "3", "name": "preview.png", "mimeType": "image/png"},
            ],
        }
        result = self._run(folder_responses, max_depth=2)
        assert len(result) == 1
        assert result[0]["name"] == "model.stl"

    def test_depth_limit_prevents_third_level(self):
        # 3 levels: parent -> child -> grandchild; max_depth=2 blocks grandchild
        folder_responses = {
            "parent": [
                {"id": "child", "name": "child-folder", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "child": [
                {"id": "grandchild", "name": "grandchild-folder", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "grandchild": [
                {"id": "1", "name": "deep.stl", "mimeType": "application/octet-stream"},
            ],
        }
        result = self._run(folder_responses, max_depth=2)
        assert result == []

    def test_depth_limit_emits_warning_log(self):
        from bot.drive.client import DriveClient
        folder_responses = {
            "parent": [
                {"id": "child", "name": "child-folder", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "child": [
                {"id": "grandchild", "name": "grandchild-folder", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "grandchild": [],
        }
        mock_service = self._make_service_with_folder_responses(folder_responses)
        with patch("bot.drive.client.Credentials.from_service_account_info"), \
             patch("bot.drive.client.build") as mock_build, \
             patch("bot.drive.client.logger") as mock_logger:
            mock_build.return_value = mock_service
            client = DriveClient(SERVICE_ACCOUNT_JSON)
            client.list_model_files_recursive("parent", max_depth=2)
        mock_logger.warning.assert_called_once()
        warning_args = str(mock_logger.warning.call_args)
        assert "grandchild" in warning_args

    def test_empty_subfolder_does_not_cause_error(self):
        folder_responses = {
            "parent": [
                {"id": "empty-sf", "name": "empty-subfolder", "mimeType": "application/vnd.google-apps.folder"},
                {"id": "nonempty-sf", "name": "nonempty-subfolder", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "empty-sf": [],
            "nonempty-sf": [
                {"id": "1", "name": "model.stl", "mimeType": "application/octet-stream"},
            ],
        }
        result = self._run(folder_responses, max_depth=2)
        assert len(result) == 1
        assert result[0]["name"] == "model.stl"

    def test_returns_id_and_name_only(self):
        folder_responses = {
            "parent": [
                {"id": "abc", "name": "model.stl", "mimeType": "application/octet-stream"},
            ]
        }
        result = self._run(folder_responses)
        assert result[0] == {"id": "abc", "name": "model.stl"}

    def test_shortcut_resolved_via_target_id(self):
        """捷徑應解析 shortcutDetails.targetId，以目標 ID 加入清單（非捷徑自身 ID）。"""
        folder_responses = {
            "parent": [
                {"id": "f1", "name": "model.stl", "mimeType": "application/octet-stream"},
                {
                    "id": "sc1",
                    "name": "copy.stl",
                    "mimeType": "application/vnd.google-apps.shortcut",
                    "shortcutDetails": {
                        "targetId": "real_file_id",
                        "targetMimeType": "application/octet-stream",
                    },
                },
            ]
        }
        result = self._run(folder_responses)
        assert len(result) == 2
        ids = {f["id"] for f in result}
        assert "f1" in ids
        assert "real_file_id" in ids  # 解析後使用目標 ID
        assert "sc1" not in ids       # 捷徑自身 ID 不應出現

    def test_shortcut_to_google_apps_type_excluded(self):
        """捷徑指向 Google Apps 類型（如 Google 文件）應被排除。"""
        folder_responses = {
            "parent": [
                {
                    "id": "sc1",
                    "name": "doc.stl",
                    "mimeType": "application/vnd.google-apps.shortcut",
                    "shortcutDetails": {
                        "targetId": "gdoc_id",
                        "targetMimeType": "application/vnd.google-apps.document",
                    },
                },
            ]
        }
        result = self._run(folder_responses)
        assert result == []

    def test_shortcut_without_target_id_excluded(self):
        """無效捷徑（沒有 targetId）應被排除。"""
        folder_responses = {
            "parent": [
                {
                    "id": "sc_broken",
                    "name": "model.stl",
                    "mimeType": "application/vnd.google-apps.shortcut",
                    "shortcutDetails": {},
                },
            ]
        }
        result = self._run(folder_responses)
        assert result == []

    def test_non_model_google_apps_file_excluded(self):
        """非捷徑的 Google Apps 類型（如 Google 文件）仍應被排除。"""
        folder_responses = {
            "parent": [
                {"id": "1", "name": "model.stl", "mimeType": "application/octet-stream"},
                {"id": "2", "name": "doc.stl",   "mimeType": "application/vnd.google-apps.document"},
            ]
        }
        result = self._run(folder_responses)
        assert len(result) == 1
        assert result[0]["id"] == "1"

    def test_default_max_depth_allows_three_levels(self):
        """Default max_depth=5 allows 3-level nesting that was blocked with old max_depth=2."""
        folder_responses = {
            "parent": [
                {"id": "child", "name": "child-folder", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "child": [
                {"id": "grandchild", "name": "grandchild-folder", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "grandchild": [
                {"id": "1", "name": "deep.stl", "mimeType": "application/octet-stream"},
            ],
        }
        result = self._run(folder_responses)  # default max_depth=5
        assert len(result) == 1
        assert result[0]["name"] == "deep.stl"

    def test_pagination_follows_next_page_token(self):
        """_scan_folder follows nextPageToken to retrieve all files beyond page 1."""
        from bot.drive.client import DriveClient

        mock_service = MagicMock()
        call_count = {"n": 0}

        def list_side_effect(**kwargs):
            result_mock = MagicMock()
            call_count["n"] += 1
            if call_count["n"] == 1:
                result_mock.execute.return_value = {
                    "files": [{"id": "1", "name": "page1.stl", "mimeType": "application/octet-stream"}],
                    "nextPageToken": "token123",
                }
            else:
                result_mock.execute.return_value = {
                    "files": [{"id": "2", "name": "page2.stl", "mimeType": "application/octet-stream"}],
                }
            return result_mock

        mock_service.files.return_value.list.side_effect = list_side_effect

        with patch("bot.drive.client.Credentials.from_service_account_info"), \
             patch("bot.drive.client.build") as mock_build:
            mock_build.return_value = mock_service
            client = DriveClient(SERVICE_ACCOUNT_JSON)
            result = client.list_model_files_recursive("folder123")

        assert len(result) == 2
        assert {f["name"] for f in result} == {"page1.stl", "page2.stl"}


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
