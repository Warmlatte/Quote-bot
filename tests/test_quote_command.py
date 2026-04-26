from unittest.mock import MagicMock, AsyncMock, patch
import discord
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_interaction(guild_id: int = 111, role_ids: list[int] | None = None):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = guild_id
    member = MagicMock(spec=discord.Member)
    roles = []
    for rid in (role_ids or []):
        r = MagicMock(spec=discord.Role)
        r.id = rid
        roles.append(r)
    member.roles = roles
    interaction.user = member
    return interaction


# ---------------------------------------------------------------------------
# _guild_check
# ---------------------------------------------------------------------------

class TestGuildCheck:
    def test_correct_guild_returns_true(self):
        from bot.commands.quote import _guild_check
        interaction = _make_interaction(guild_id=111)
        assert _guild_check(interaction, 111) is True

    def test_wrong_guild_returns_false(self):
        from bot.commands.quote import _guild_check
        interaction = _make_interaction(guild_id=999)
        assert _guild_check(interaction, 111) is False


# ---------------------------------------------------------------------------
# _role_check
# ---------------------------------------------------------------------------

class TestRoleCheck:
    def test_user_with_role_returns_true(self):
        from bot.commands.quote import _role_check
        interaction = _make_interaction(role_ids=[42, 99])
        assert _role_check(interaction, 42) is True

    def test_user_without_role_returns_false(self):
        from bot.commands.quote import _role_check
        interaction = _make_interaction(role_ids=[99])
        assert _role_check(interaction, 42) is False

    def test_no_roles_returns_false(self):
        from bot.commands.quote import _role_check
        interaction = _make_interaction(role_ids=[])
        assert _role_check(interaction, 42) is False


# ---------------------------------------------------------------------------
# _build_quote_embed
# ---------------------------------------------------------------------------

BASE_EMBED_KWARGS = dict(
    quote_number="Q20260426-001",
    customer_name="測試客戶",
    resin_label="RPG高精度樹脂",
    body_count=3,
    material_cost=444,
    processing_fee=230,
    subtotal=674,
    auto_discount_amount=0,
    manual_discount="無",
    final_total=674,
    order_status="正常",
    file_details=[
        {"filename": "a.stl", "volume_ml": 100.0, "body_count": 2},
        {"filename": "b.stl", "volume_ml": 27.5, "body_count": 1},
    ],
    error_files=[],
)


class TestBuildQuoteEmbed:
    def _build(self, **overrides):
        from bot.commands.quote import _build_quote_embed
        return _build_quote_embed(**{**BASE_EMBED_KWARGS, **overrides})

    def test_returns_discord_embed(self):
        embed = self._build()
        assert isinstance(embed, discord.Embed)

    def test_title_contains_quote_number(self):
        embed = self._build(quote_number="Q-XYZ")
        assert "Q-XYZ" in embed.title

    def test_contains_customer_name(self):
        embed = self._build(customer_name="VIP客戶")
        field_values = [f.value for f in embed.fields]
        assert any("VIP客戶" in v for v in field_values)

    def test_contains_final_total(self):
        embed = self._build(final_total=1234)
        field_values = [f.value for f in embed.fields]
        assert any("1234" in v for v in field_values)

    def test_contains_order_status(self):
        embed = self._build(order_status="未達低消")
        field_values = [f.value for f in embed.fields]
        assert any("未達低消" in v for v in field_values)

    def test_no_discount_field_when_zero(self):
        embed = self._build(auto_discount_amount=0, manual_discount="無")
        field_names = [f.name for f in embed.fields]
        assert not any("折扣" in n for n in field_names)

    def test_auto_discount_field_shown_when_nonzero(self):
        embed = self._build(auto_discount_amount=350)
        field_names = [f.name for f in embed.fields]
        assert any("折扣" in n for n in field_names)

    def test_manual_discount_field_shown_when_set(self):
        embed = self._build(manual_discount="九折+免運")
        field_names = [f.name for f in embed.fields]
        assert any("折扣" in n for n in field_names)

    def test_error_files_field_shown(self):
        embed = self._build(error_files=["broken.stl", "bad.obj"])
        field_names = [f.name for f in embed.fields]
        assert any("異常" in n for n in field_names)

    def test_no_error_files_field_when_empty(self):
        embed = self._build(error_files=[])
        field_names = [f.name for f in embed.fields]
        assert not any("異常" in n for n in field_names)

    def test_file_details_shown(self):
        embed = self._build(
            file_details=[{"filename": "model.stl", "volume_ml": 50.0, "body_count": 1}]
        )
        field_values = [f.value for f in embed.fields]
        assert any("model.stl" in v for v in field_values)

    def test_file_details_capped_at_10(self):
        many = [{"filename": f"m{i}.stl", "volume_ml": 1.0, "body_count": 1} for i in range(15)]
        embed = self._build(file_details=many)
        detail_fields = [f for f in embed.fields if "m" in f.value.lower() and ".stl" in f.value.lower()]
        shown_files = sum(f.value.count(".stl") for f in embed.fields)
        assert shown_files <= 10
