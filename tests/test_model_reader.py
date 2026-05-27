"""
tests/test_model_reader.py

TDD 測試：bot.pricing.model_reader 模組
測試 ModelReadResult dataclass 與 read_models() 函式。

業務規則（來自 CLAUDE.md）：
- 禁止使用 mesh.body_count，改用 mesh.split(only_watertight=False) 計件
- 以 len(faces) >= 100 過濾雜訊碎片，取有效連通分量數作為 body_count
- 體積單位：trimesh 回傳 mm³，需 ÷1000 轉換為 ml
- 非 watertight 模型不視為損毀；僅載入失敗或所有分量被過濾時加入 error_files
- 計算移至 asyncio.run_in_executor 避免阻塞 Discord 事件迴圈
"""

import io
import math
import pathlib
import struct
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import trimesh
import trimesh.remesh

from bot.pricing.model_reader import ModelReadResult, _load_model_sync, read_models


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _make_binary_stl(triangles: list[tuple]) -> bytes:
    """產生合法的 binary STL bytes。triangles 為 (normal, v0, v1, v2) 的 list。"""
    header = b"\x00" * 80
    count = struct.pack("<I", len(triangles))
    body = b""
    for normal, v0, v1, v2 in triangles:
        body += struct.pack("<fff", *normal)
        body += struct.pack("<fff", *v0)
        body += struct.pack("<fff", *v1)
        body += struct.pack("<fff", *v2)
        body += struct.pack("<H", 0)  # attribute byte count
    return header + count + body


def _unit_cube_stl() -> bytes:
    """
    製作一個 10mm × 10mm × 10mm 立方體的 binary STL。
    細分兩次使 faces = 192（>= 100），通過 body_count 面數過濾器。
    """
    mesh = trimesh.creation.box(extents=[10, 10, 10])
    for _ in range(2):
        v, f = trimesh.remesh.subdivide(mesh.vertices, mesh.faces)
        mesh = trimesh.Trimesh(vertices=v, faces=f)
    buf = io.BytesIO()
    mesh.export(buf, file_type="stl")
    return buf.getvalue()


def _make_temp_stl(tmp_path: pathlib.Path, filename: str, content: bytes) -> pathlib.Path:
    p = tmp_path / filename
    p.write_bytes(content)
    return p


# ─── ModelReadResult 單元測試 ─────────────────────────────────────────────────

class TestModelReadResult:
    def test_fields_accessible(self):
        result = ModelReadResult(
            filename="test.stl",
            volume_ml=12.5,
            body_count=3,
        )
        assert result.filename == "test.stl"
        assert result.volume_ml == 12.5
        assert result.body_count == 3

    def test_is_frozen_dataclass(self):
        result = ModelReadResult(filename="x.stl", volume_ml=1.0, body_count=1)
        with pytest.raises((FrozenInstanceError, AttributeError)):
            result.filename = "y.stl"  # type: ignore[misc]

    def test_volume_ml_float(self):
        result = ModelReadResult(filename="a.stl", volume_ml=0.001, body_count=1)
        assert isinstance(result.volume_ml, float)

    def test_body_count_int(self):
        result = ModelReadResult(filename="b.stl", volume_ml=5.0, body_count=2)
        assert isinstance(result.body_count, int)


# ─── read_models：正常流程 ────────────────────────────────────────────────────

class TestReadModels:
    @pytest.mark.asyncio
    async def test_single_valid_stl(self, tmp_path):
        stl_bytes = _unit_cube_stl()
        p = _make_temp_stl(tmp_path, "cube.stl", stl_bytes)

        results, error_files = await read_models([p])

        assert len(results) == 1
        assert len(error_files) == 0
        result = results[0]
        assert result.filename == "cube.stl"
        # 10×10×10 mm³ = 1000 mm³ = 1.0 ml
        assert math.isclose(result.volume_ml, 1.0, rel_tol=0.05)
        assert result.body_count >= 1

    @pytest.mark.asyncio
    async def test_volume_unit_is_ml_not_mm3(self, tmp_path):
        """確認回傳的是 ml（mm³ ÷ 1000），而非 mm³。"""
        stl_bytes = _unit_cube_stl()
        p = _make_temp_stl(tmp_path, "cube.stl", stl_bytes)

        results, _ = await read_models([p])

        # 10^3 mm³ = 1 ml；若錯誤回傳 mm³ 則值為 1000
        assert results[0].volume_ml < 100  # 合理的 ml 值，不可能是 mm³

    @pytest.mark.asyncio
    async def test_multiple_valid_files(self, tmp_path):
        stl_bytes = _unit_cube_stl()
        files = [
            _make_temp_stl(tmp_path, f"cube_{i}.stl", stl_bytes)
            for i in range(3)
        ]

        results, error_files = await read_models(files)

        assert len(results) == 3
        assert len(error_files) == 0

    @pytest.mark.asyncio
    async def test_empty_file_list(self):
        results, error_files = await read_models([])

        assert results == []
        assert error_files == []

    @pytest.mark.asyncio
    async def test_filename_preserved(self, tmp_path):
        stl_bytes = _unit_cube_stl()
        p = _make_temp_stl(tmp_path, "my_model.stl", stl_bytes)

        results, _ = await read_models([p])

        assert results[0].filename == "my_model.stl"


# ─── read_models：錯誤處理 ────────────────────────────────────────────────────

class TestReadModelsErrorHandling:
    @pytest.mark.asyncio
    async def test_corrupted_file_goes_to_error_files(self, tmp_path):
        """損毀/非 watertight 且體積 ≤ 0 的檔案加入 error_files，不中斷流程。"""
        bad_path = tmp_path / "bad.stl"
        bad_path.write_bytes(b"this is not a valid stl file at all")

        results, error_files = await read_models([bad_path])

        assert len(results) == 0
        assert "bad.stl" in error_files

    @pytest.mark.asyncio
    async def test_mix_valid_and_invalid(self, tmp_path):
        good_stl = _unit_cube_stl()
        good_path = _make_temp_stl(tmp_path, "good.stl", good_stl)

        bad_path = tmp_path / "bad.stl"
        bad_path.write_bytes(b"garbage data")

        results, error_files = await read_models([good_path, bad_path])

        assert len(results) == 1
        assert results[0].filename == "good.stl"
        assert "bad.stl" in error_files

    @pytest.mark.asyncio
    async def test_non_watertight_mesh_goes_to_error(self, tmp_path):
        """所有 shell < 100 faces（如單一三角形）→ 應加入 error_files。"""
        # 製作一個只有 1 個面的 mesh，split 後 faces < 100 → 被過濾 → error_files
        open_mesh = trimesh.Trimesh(
            vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            faces=[[0, 1, 2]],
        )
        buf = io.BytesIO()
        open_mesh.export(buf, file_type="stl")

        bad_path = tmp_path / "open_mesh.stl"
        bad_path.write_bytes(buf.getvalue())

        results, error_files = await read_models([bad_path])

        # 1 face < 100 → 全部分量被過濾 → error
        assert "open_mesh.stl" in error_files

    @pytest.mark.asyncio
    async def test_error_does_not_stop_remaining_files(self, tmp_path):
        """第一個檔案損毀，後續檔案仍要繼續處理。"""
        bad_path = tmp_path / "first.stl"
        bad_path.write_bytes(b"bad")

        good_stl = _unit_cube_stl()
        good_path = _make_temp_stl(tmp_path, "second.stl", good_stl)

        results, error_files = await read_models([bad_path, good_path])

        assert len(results) == 1
        assert results[0].filename == "second.stl"
        assert "first.stl" in error_files


# ─── read_models：非同步行為 ──────────────────────────────────────────────────

class TestReadModelsAsync:
    @pytest.mark.asyncio
    async def test_returns_coroutine(self, tmp_path):
        """read_models 必須是 async 函式，可被 await。"""
        import inspect
        stl_bytes = _unit_cube_stl()
        p = _make_temp_stl(tmp_path, "cube.stl", stl_bytes)
        coro = read_models([p])
        assert inspect.isawaitable(coro), "read_models 應回傳可 await 的 coroutine"
        await coro  # consume it

    @pytest.mark.asyncio
    async def test_uses_executor_for_cpu_bound_work(self, tmp_path):
        """trimesh 計算應透過 run_in_executor 執行，不阻塞事件迴圈。"""
        stl_bytes = _unit_cube_stl()
        p = _make_temp_stl(tmp_path, "cube.stl", stl_bytes)

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value = MagicMock()
            mock_loop.return_value.run_in_executor = AsyncMock(
                return_value=ModelReadResult(
                    filename="cube.stl",
                    volume_ml=1.0,
                    body_count=1,
                )
            )
            results, error_files = await read_models([p])

        # 只要 run_in_executor 有被呼叫即可
        mock_loop.return_value.run_in_executor.assert_called()


# ─── body_count：整合測試（使用真實 STL）────────────────────────────────────

class TestBodyCount:
    @pytest.mark.asyncio
    async def test_body_count_single_body(self, tmp_path):
        """單一 watertight 物件應回傳 body_count >= 1。"""
        stl_bytes = _unit_cube_stl()
        p = _make_temp_stl(tmp_path, "single.stl", stl_bytes)

        results, _ = await read_models([p])

        assert results[0].body_count >= 1


# ─── body_count：使用 split() + faces >= 100 過濾（新實作）─────────────────────

class TestBodyCountWithSplit:
    """新件數計算：mesh.split(only_watertight=False) + len(faces) >= 100 過濾。"""

    def _make_shell(self, face_count: int, volume: float = 1000.0):
        shell = MagicMock()
        shell.faces = list(range(face_count))
        shell.volume = volume
        return shell

    def _make_mock_mesh(self, shells_faces: list = None, shell_volumes: list = None):
        if shells_faces is None:
            shells_faces = [100]
        if shell_volumes is None:
            shell_volumes = [1000.0] * len(shells_faces)
        mesh = MagicMock(spec=trimesh.Trimesh)
        mesh.split.return_value = [
            self._make_shell(n, v) for n, v in zip(shells_faces, shell_volumes)
        ]
        return mesh

    def test_body_count_uses_split_single_shell(self, tmp_path):
        """單一 >= 100 faces shell → body_count == 1。"""
        mock_mesh = self._make_mock_mesh(shells_faces=[100])
        p = tmp_path / "test.stl"
        p.write_bytes(b"dummy")

        with patch("bot.pricing.model_reader.trimesh.load", return_value=mock_mesh):
            result = _load_model_sync(p)

        assert result.body_count == 1

    def test_body_count_uses_split_two_shells(self, tmp_path):
        """兩個各 >= 100 faces 的 shell → body_count == 2。"""
        mock_mesh = self._make_mock_mesh(shells_faces=[100, 150])
        p = tmp_path / "test.stl"
        p.write_bytes(b"dummy")

        with patch("bot.pricing.model_reader.trimesh.load", return_value=mock_mesh):
            result = _load_model_sync(p)

        assert result.body_count == 2

    def test_body_count_noise_shell_filtered(self, tmp_path):
        """一個 >= 100 faces + 一個 < 100 faces 碎片 → body_count == 1。"""
        mock_mesh = self._make_mock_mesh(shells_faces=[100, 50])
        p = tmp_path / "test.stl"
        p.write_bytes(b"dummy")

        with patch("bot.pricing.model_reader.trimesh.load", return_value=mock_mesh):
            result = _load_model_sync(p)

        assert result.body_count == 1

    @pytest.mark.asyncio
    async def test_body_count_all_noise_raises(self, tmp_path):
        """全部 < 100 faces → _load_model_sync 拋出 ValueError；read_models 加入 error_files。"""
        mock_mesh = self._make_mock_mesh(shells_faces=[50, 30])
        p = tmp_path / "test.stl"
        p.write_bytes(b"dummy")

        with patch("bot.pricing.model_reader.trimesh.load", return_value=mock_mesh):
            with pytest.raises(ValueError):
                _load_model_sync(p)

            results, error_files = await read_models([p])

        assert len(results) == 0
        assert "test.stl" in error_files

    @pytest.mark.asyncio
    async def test_non_watertight_is_valid(self, tmp_path):
        """非 watertight 但有 >= 100 faces 的 shell → 有效 ModelReadResult，非 error。"""
        mock_mesh = self._make_mock_mesh(shells_faces=[100], shell_volumes=[500.0])
        mock_mesh.is_watertight = False
        p = tmp_path / "test.stl"
        p.write_bytes(b"dummy")

        with patch("bot.pricing.model_reader.trimesh.load", return_value=mock_mesh):
            results, error_files = await read_models([p])

        assert len(results) == 1
        assert error_files == []
        assert results[0].body_count == 1
        # volume comes from the part, not the whole mesh
        assert math.isclose(results[0].volume_ml, 0.5, rel_tol=0.01)  # 500mm³ ÷ 1000 = 0.5ml

    def test_volume_summed_from_parts_not_whole_mesh(self, tmp_path):
        """體積必須從各有效 part 加總，而非整個合併 mesh 的 volume（避免多件互消歸零）。"""
        # 兩個 part，各 1000mm³；整體 mesh 不提供 volume
        mock_mesh = self._make_mock_mesh(shells_faces=[100, 200], shell_volumes=[1000.0, 2000.0])
        p = tmp_path / "test.stl"
        p.write_bytes(b"dummy")

        with patch("bot.pricing.model_reader.trimesh.load", return_value=mock_mesh):
            result = _load_model_sync(p)

        # 1000+2000 = 3000mm³ ÷ 1000 = 3.0ml
        assert math.isclose(result.volume_ml, 3.0, rel_tol=0.01)
        assert result.body_count == 2
