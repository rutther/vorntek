"""统一客户价值分引擎（客户公海 · 21 列标准通道）。

全库唯一的 value 算法。口径锚点＝2026-09-15 生成「新表-客资标准格式」的
transform_v2.score()：底分 10 + 渠道 + 身份 I + 证据 V + 路线层级 + WhatsApp
已确认 + 有邮箱 + 优先级 P + 项目/采购信号，1–2000 一位小数取整。

21 列投影丢掉了「项目/采购信号」的源证据列；导入对账时用
derive_project_signal() 以差值 0/+5 把它还原成显式标记，保证引擎输出与
标准表逐行一致，而不是静默照抄文件值。
"""

from decimal import Decimal

VALUE_BASIS_PROXY = 'proxy'
VALUE_FLOOR = Decimal('1.0')
VALUE_CEILING = Decimal('2000.0')

CHANNEL_SCORES = {
    'website_form': 30,
    'website': 30,
    'manual': 20,
    'research_public': 0,
    'research': 0,
}
IDENTITY_SCORES = {'I3': 15, 'I2': 8}
EVIDENCE_SCORES = {'V1': 10, 'V2': 6, 'V3': 2}
TIER_SCORES = {'本人直联': 15, '公开业务手机': 12, '指定人物转接': 10, '企业总机': 6, '部门入口': 4}
PRIORITY_SCORES = {'P1': 10, 'P2': 5}
WHATSAPP_CONFIRMED = '已确认'
PROJECT_SIGNAL_POINTS = 5


class ValueReconciliationError(ValueError):
    def __init__(self, delta: Decimal, message: str = ''):
        self.delta = delta
        super().__init__(message or f'value 复算差 {delta}，标准表只允许 0 或 +5（项目/采购信号）。')


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def compute_customer_value(
    *,
    source_channel: str = '',
    identity: str = '',
    evidence_v: str = '',
    route_tier: str = '',
    whatsapp_confirmed: str = '',
    has_email: bool = False,
    priority: str = '',
    project_signal: bool = False,
) -> Decimal:
    points = 10
    points += CHANNEL_SCORES.get(_text(source_channel), 0)
    points += IDENTITY_SCORES.get(_text(identity), 0)
    points += EVIDENCE_SCORES.get(_text(evidence_v), 0)
    points += TIER_SCORES.get(_text(route_tier), 0)
    if _text(whatsapp_confirmed) == WHATSAPP_CONFIRMED:
        points += 5
    if has_email:
        points += 5
    points += PRIORITY_SCORES.get(_text(priority), 0)
    if project_signal:
        points += PROJECT_SIGNAL_POINTS
    value = Decimal(points).quantize(Decimal('0.1'))
    return max(VALUE_FLOOR, min(VALUE_CEILING, value))


def derive_project_signal(*, file_value, recomputed: Decimal) -> bool:
    """以 0/+5 差值还原「项目/采购信号」标记；其他差值视为数据异常。"""

    delta = Decimal(str(file_value)) - recomputed
    if delta == 0:
        return False
    if delta == PROJECT_SIGNAL_POINTS:
        return True
    raise ValueReconciliationError(delta)


def reconcile_file_value(*, file_value, **factors) -> tuple[Decimal, bool]:
    """标准表导入对账：返回 (引擎最终值, project_signal)，并保证等于文件值。"""

    base = compute_customer_value(**factors, project_signal=False)
    signal = derive_project_signal(file_value=file_value, recomputed=base)
    final = compute_customer_value(**factors, project_signal=signal)
    expected = Decimal(str(file_value))
    if final != expected:
        raise ValueReconciliationError(expected - final, f'引擎值 {final} 与文件值 {expected} 不一致。')
    return final, signal
