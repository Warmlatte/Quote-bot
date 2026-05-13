# 手動修改件數功能設計

**日期：** 2026-05-13
**狀態：** 已確認

## 背景

trimesh 的件數計算（`mesh.split(only_watertight=False)`）偶有誤差，操作者需能在發出估價前手動修正個別檔案的件數，並讓所有相關費用即時重算。

## 設計範圍

僅修改 `bot/commands/quote.py`，新增三個元件並調整 `QuoteActionView`：

1. `BodyCountSelectView` — ephemeral 介面，讓操作者選擇要修改的檔案
2. `BodyCountModal` — 輸入新件數的 Modal
3. `QuoteActionView` 新增 `🔢 件數` 按鈕，並加入重算邏輯

不修改 `pricing/engine.py`、`pdf_gen/generator.py`、`db/client.py` 或任何其他模組。

---

## 元件設計

### `BodyCountSelectView`

- ephemeral 訊息顯示
- `discord.ui.Select`：選項為 `_file_details` 每個檔案的 `filename`，`value = str(index)`，最多 25 個選項（實務上遠低於此限制）
- `✅ 確認` 按鈕：初始 `disabled=True`，選取後啟用
- `❌ 取消` 按鈕：點擊後 `edit_message` 為「已取消。」

### `BodyCountModal`

- 標題：「修改件數」
- 單一 `TextInput`：label「件數」，`default = str(selected_file["body_count"])`，`max_length=10`

### `QuoteActionView` 修改

#### 新增按鈕（row 0）

```
[✏️ 折扣]  [🚚 運送]  [🔢 件數]
[✅ 接受報價]  [❌ 拒絕報價]
```

#### 提交後重算流程

`BodyCountModal.on_submit` 執行以下步驟：

```python
# 1. 驗證輸入（正整數）
new_count = int(raw)
if new_count <= 0:
    raise ValueError

# 2. 建立新的 file_details（不可變原則）
new_file_details = [
    {**f, "body_count": new_count} if i == selected_idx else f
    for i, f in enumerate(av._file_details)
]

# 3. 重算 quote
total_bodies = sum(f["body_count"] for f in new_file_details)
new_quote = calculate_quote(
    resin=av._quote_result.resin,
    colored=av._quote_result.colored,
    volume_ml=av._quote_result.volume_ml,
    body_count=total_bodies,
)

# 4. 重算手動折扣（若有）
if av._manual_discount.mode != "none":
    _, av._manual_discount_amount = apply_manual_discount(
        new_quote.final_total, av._manual_discount
    )

# 5. 更新狀態並刷新 Embed
av._file_details = new_file_details
av._quote_result = new_quote
await av._refresh_embed()
```

---

## 影響範圍確認

| 項目 | 行為 |
|---|---|
| `processing_fee` | 由 `calculate_quote()` 重算 |
| `subtotal` | 由 `calculate_quote()` 重算 |
| `auto_discount_amount` | 由 `calculate_quote()` 重算 |
| `order_status` | 由 `calculate_quote()` 重算 |
| `final_total` | 由 `calculate_quote()` 重算 |
| `_manual_discount_amount` | 以新 `final_total` 重算 |
| `material_cost` | 不變（只與 volume_ml + resin 有關） |
| `_shipping_fee` / `_shipping_address` | 不變（操作者手動設值） |
| `_shipping_free_label` | 不變（操作者操作時設值） |
| PDF 件數顯示 | 採用更新後的 `_file_details`，正確反映修改 |
| DB 記錄 (`body_count`, `processing_fee` 等) | 採用更新後的 `_quote_result`，正確反映修改 |

## 輸入驗證

- 非數字、負數、零 → 回應 `❌ 無效的件數，請輸入正整數（如 3）。`，ephemeral

## 不含的功能

- 還原至原始計算件數（不需要）
- 修改後記錄異動歷程（不需要）
