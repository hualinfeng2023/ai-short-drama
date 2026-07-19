from app.services.character_visuals import DOSSIER_VIEWS


def test_expression_dossier_prompt_requires_visibly_distinct_quadrants() -> None:
    instruction = dict(DOSSIER_VIEWS)["EXPRESSIONS"]

    assert "2×2 等分四宫格" in instruction
    assert "左上中性" in instruction
    assert "右上微笑" in instruction
    assert "左下警觉" in instruction
    assert "右下悲伤" in instruction
    assert "缩略图尺寸下也一眼可辨" in instruction
    assert "不得出现四张近似中性微表情" in instruction
    assert "不得改变脸型、五官、年龄感" in instruction
