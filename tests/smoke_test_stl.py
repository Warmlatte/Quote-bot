"""
STL Smoke Test — LAT-147
依據 openspec/specs/model-reader-smoke-test/spec.md 的預期值驗證。
執行方式：python tests/smoke_test_stl.py
不納入 pytest 自動收集。
"""
import math
import os
import sys

import trimesh

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot.pricing.engine import ResinType, calculate_quote

DATA_ROOT = "/Users/yan/Documents/300 TheRollBer/350 專案資源/355 3D Quote bot 估價機器人/355-5 測試資料"

_PASS = 0
_FAIL = 0


def check(label: str, actual, expected) -> bool:
    global _PASS, _FAIL
    if actual == expected:
        print(f"  ✅ {label}: {actual}")
        _PASS += 1
        return True
    else:
        print(f"  ❌ {label}: got {actual!r}, expected {expected!r}")
        _FAIL += 1
        return False


def read_stl_files(folder: str) -> tuple[list[dict], list[str]]:
    """簡化版模型讀取：回傳 (valid_list, error_files)"""
    valid, errors = [], []
    for fname in sorted(os.listdir(folder)):
        if not fname.lower().endswith((".stl", ".obj")):
            continue
        path = os.path.join(folder, fname)
        try:
            mesh = trimesh.load(path, force="mesh")
            vol = mesh.volume / 1000
            if not mesh.is_watertight or vol <= 0:
                errors.append(fname)
            else:
                valid.append({"filename": fname, "volume_ml": vol, "body_count": mesh.body_count})
        except Exception:
            errors.append(fname)
    return valid, errors


# ── Scenario A — test3d 原始模型，RPG ────────────────────────────────────────
print("\n=== Scenario A: test3d / RPG ===")
valid, errors = read_stl_files(os.path.join(DATA_ROOT, "test3d"))

check("valid 數量", len(valid), 2)
check("error 數量", len(errors), 1)
check("error 檔名", errors[0], "Bodoluk_leg_l.stl")

total_vol = sum(f["volume_ml"] for f in valid)
total_body = sum(f["body_count"] for f in valid)
check("total_body_count", total_body, 3)

result = calculate_quote(ResinType.RPG, colored=False, volume_ml=total_vol, body_count=total_body)
check("material_cost", result.material_cost, 444)
check("processing_fee", result.processing_fee, 230)
check("subtotal", result.subtotal, 674)
check("order_status", result.order_status, "正常")
check("final_total", result.final_total, 674)

# ── Scenario B — test3d2 Chitubox 無支撐，CLEAR 不調色 ───────────────────────
print("\n=== Scenario B: test3d2 / CLEAR 不調色 ===")
valid2, errors2 = read_stl_files(os.path.join(DATA_ROOT, "test3d2"))

check("valid 數量", len(valid2), 2)
check("error 數量", len(errors2), 1)
check("error 檔名", errors2[0], "default__02.stl")

total_vol2 = sum(f["volume_ml"] for f in valid2)
total_body2 = sum(f["body_count"] for f in valid2)
result2 = calculate_quote(ResinType.CLEAR, colored=False, volume_ml=total_vol2, body_count=total_body2)
check("material_cost", result2.material_cost, 444)
check("processing_fee", result2.processing_fee, 230)
check("final_total", result2.final_total, 674)

# ── Scenario C — test3d3 Chitubox 含支撐，全失敗 ─────────────────────────────
print("\n=== Scenario C: test3d3 / 含支撐（預期全 error） ===")
valid3, errors3 = read_stl_files(os.path.join(DATA_ROOT, "test3d3"))

check("valid 數量（應為 0）", len(valid3), 0)
check("error 數量（應為 3）", len(errors3), 3)

# ── Scenario D — 單檔 base_scenic，CLEAR 調色對照 ────────────────────────────
print("\n=== Scenario D: 單檔 Bodoluk_base_scenic_1 / CLEAR 調色 ===")
single = next(f for f in valid if "base_scenic" in f["filename"])
result_d = calculate_quote(ResinType.CLEAR, colored=True, volume_ml=single["volume_ml"], body_count=single["body_count"])
# ceil(125.5079)=126, 126*7.0=882; processing 2件=160; subtotal=1042
check("material_cost", result_d.material_cost, 882)
check("processing_fee", result_d.processing_fee, 160)
check("subtotal", result_d.subtotal, 1042)
check("order_status", result_d.order_status, "正常")
check("final_total", result_d.final_total, 1042)

# ── 結果摘要 ──────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"結果：{_PASS} PASS / {_FAIL} FAIL")
if _FAIL == 0:
    print("✅ 所有情境通過，trimesh + 計價引擎整合正常")
else:
    print("❌ 有失敗項目，請檢查上方輸出")
sys.exit(0 if _FAIL == 0 else 1)
