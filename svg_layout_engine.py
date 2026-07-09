import copy
import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from dxf_utils import parse_sku
from dxf_layout_engine import (
    ESPACAMENTO_DXF_MESMO_FURO,
    ESPACAMENTO_LINHA_COR,
    ESPACAMENTO_PLANO_COR,
    MARGEM_ESQUERDA,
    MARGEM_SUPERIOR,
    MARGEM_INFERIOR,
    ITEM_DXF_FIXED_WIDTH_MM,
    ITEM_DXF_FIXED_HEIGHT_MM,
    PLANO_DXF_FIXED_WIDTH_MM,
    PLANO_DXF_FIXED_HEIGHT_MM,
    BARRA_DXF_FIXED_WIDTH_MM,
    BARRA_DXF_FIXED_HEIGHT_MM,
    ESPACAMENTO_SEPARADOR_PRE_BARRA,
    ESPACAMENTO_SEPARADOR_POST_BARRA,
    ADJUSTMENT_OFFSET_BARRA_MM,
    NoEntitiesFoundError,
)
from google_drive_utils import baixar_arquivo_drive, buscar_arquivo_personalizado_por_id_e_sku

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

BARRA_SVG_PATH = os.path.join("Plano_Info", "Barra.svg")
PLANO_TEXTO_ALTURA_MM = 34.0
PLANO_TEXTO_TAMANHO_FONTE_MM = 18.0
PLANO_TEXTO_X_MM = MARGEM_ESQUERDA
PLANO_TEXTO_Y_MM = MARGEM_SUPERIOR
PLANO_TEXTO_CAIXA_ALTURA_MM = 28.0

_barra_svg_cache = None


def _fmt(value: float) -> str:
    """Formata número para SVG sem lixo de casas decimais."""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _parse_length_mm(value: Any) -> float | None:
    """
    Converte medidas comuns de SVG para milímetros.
    Quando não existe unidade, assume que a unidade do arquivo já está em mm,
    que é o padrão esperado para os arquivos enviados ao plano de gravação.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    match = re.match(r"^([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)([a-zA-Z%]*)$", text)
    if not match:
        return None

    number = float(match.group(1))
    unit = match.group(2).lower()

    if unit in ("", "mm"):
        return number
    if unit == "cm":
        return number * 10.0
    if unit == "in":
        return number * 25.4
    if unit == "pt":
        return number * 25.4 / 72.0
    if unit == "pc":
        return number * 25.4 / 6.0
    if unit == "px":
        return number * 25.4 / 96.0

    return None


def _parse_viewbox(root: ET.Element) -> Tuple[float, float, float, float] | None:
    view_box = root.attrib.get("viewBox") or root.attrib.get("viewbox")
    if not view_box:
        return None

    values = re.split(r"[\s,]+", view_box.strip())
    if len(values) != 4:
        return None

    try:
        return tuple(float(v) for v in values)  # type: ignore[return-value]
    except ValueError:
        return None


def _svg_dimensions_mm(root: ET.Element, fallback_width: float, fallback_height: float) -> Tuple[float, float, float, float]:
    """
    Retorna (min_x, min_y, width_mm, height_mm) para posicionamento.
    O conteúdo SVG em si não é convertido; essas medidas só orientam o layout.
    """
    width = _parse_length_mm(root.attrib.get("width"))
    height = _parse_length_mm(root.attrib.get("height"))
    view_box = _parse_viewbox(root)

    min_x = 0.0
    min_y = 0.0

    if view_box:
        min_x, min_y, vb_width, vb_height = view_box
        if width is None and vb_width > 0:
            width = vb_width
        if height is None and vb_height > 0:
            height = vb_height

    if width is None or width <= 0:
        width = fallback_width
    if height is None or height <= 0:
        height = fallback_height

    return min_x, min_y, float(width), float(height)


def _load_svg_root(caminho_svg: str, fallback_width: float, fallback_height: float) -> Dict[str, Any]:
    tree = ET.parse(caminho_svg)
    root = tree.getroot()

    # Aceita SVG com ou sem namespace. Se não for <svg>, falha para evitar arquivo inválido.
    tag_name = root.tag.split("}")[-1].lower()
    if tag_name != "svg":
        raise ValueError(f"Arquivo '{caminho_svg}' não parece ser um SVG válido: tag raiz '{root.tag}'.")

    min_x, min_y, width, height = _svg_dimensions_mm(root, fallback_width, fallback_height)
    return {
        "root": root,
        "width": width,
        "height": height,
        "original_min_x": min_x,
        "original_min_y": min_y,
    }


def _tag_name(element: ET.Element) -> str:
    """Retorna o nome local da tag, ignorando namespace."""
    return str(element.tag).split("}")[-1].lower()


def _prepare_nested_svg(svg_data: Dict[str, Any], x: float, y: float) -> ET.Element:
    source_root = copy.deepcopy(svg_data["root"])

    # LightBurn costuma interpretar mal <svg> dentro de <svg> usando x/y.
    # Por isso o arquivo importado vira um <g transform=...> normal, que é mais
    # compatível com Corel, LightBurn e outros leitores SVG.
    wrapper = ET.Element(f"{{{SVG_NS}}}g")
    wrapper.set("transform", f"translate({_fmt(x)},{_fmt(y)})")
    wrapper.set("pointer-events", "all")
    wrapper.set("data-locked", "false")

    # Leva atributos visuais simples do <svg> original para o grupo, mas não leva
    # width/height/viewBox/x/y, porque agora o posicionamento é feito por transform.
    ignored_root_attrs = {
        "x", "y", "width", "height", "viewBox", "viewbox", "version",
        "xmlns", "xmlns:xlink", "overflow", "preserveAspectRatio",
    }
    for attr_name, attr_value in source_root.attrib.items():
        if attr_name in ignored_root_attrs or attr_name.startswith("xmlns"):
            continue
        wrapper.set(attr_name, attr_value)

    _unlock_svg_tree(source_root)

    view_box = _parse_viewbox(source_root)
    content_parent = wrapper

    if view_box:
        min_x, min_y, vb_width, vb_height = view_box
        if vb_width > 0 and vb_height > 0:
            scale_x = svg_data["width"] / vb_width
            scale_y = svg_data["height"] / vb_height
            needs_matrix = (
                abs(scale_x - 1.0) > 0.0001
                or abs(scale_y - 1.0) > 0.0001
                or abs(min_x) > 0.0001
                or abs(min_y) > 0.0001
            )
            if needs_matrix:
                content_parent = ET.SubElement(wrapper, f"{{{SVG_NS}}}g")
                # Equivale ao comportamento do viewBox: (coord - min) * escala.
                content_parent.set(
                    "transform",
                    f"matrix({_fmt(scale_x)} 0 0 {_fmt(scale_y)} {_fmt(-min_x * scale_x)} {_fmt(-min_y * scale_y)})",
                )
                content_parent.set("pointer-events", "all")
                content_parent.set("data-locked", "false")

    for child in list(source_root):
        source_root.remove(child)
        content_parent.append(child)

    return wrapper


def _sanitize_style_for_editing(style_value: str) -> str:
    """
    Remove travas comuns de edição/seleção e propriedades de texto que alguns
    importadores SVG, principalmente o LightBurn, interpretam mal.
    """
    props_to_remove = {
        "pointer-events",
        "user-select",
        "-webkit-user-select",
        "-moz-user-select",
        "-ms-user-select",
        "dominant-baseline",
        "alignment-baseline",
        "baseline-shift",
        "text-length",
        "textlength",
        "length-adjust",
        "lengthadjust",
    }

    cleaned_parts = []
    for raw_part in style_value.split(";"):
        part = raw_part.strip()
        if not part:
            continue

        prop_name = part.split(":", 1)[0].strip().lower()
        if prop_name in props_to_remove:
            continue

        # Mantém compatibilidade com a regra antiga caso venha sem separador claro.
        lower = part.lower().replace(" ", "")
        if lower.startswith("pointer-events:none"):
            continue

        cleaned_parts.append(part)

    return "; ".join(cleaned_parts)


TEXT_ATTRS_TO_REMOVE_FOR_LIGHTBURN = {
    "dominant-baseline",
    "alignment-baseline",
    "baseline-shift",
    "textLength",
    "textlength",
    "lengthAdjust",
    "lengthadjust",
}


def _first_svg_number(value: Any) -> float | None:
    if value is None:
        return None
    match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _font_size_number(attrs: Dict[str, str], fallback: float = 12.0) -> float:
    font_size = attrs.get("font-size")
    if not font_size and attrs.get("style"):
        match = re.search(r"font-size\s*:\s*([^;]+)", attrs["style"], re.IGNORECASE)
        if match:
            font_size = match.group(1)

    parsed = _first_svg_number(font_size)
    return parsed if parsed and parsed > 0 else fallback


def _parse_svg_delta(value: Any, font_size: float) -> float | None:
    if value is None:
        return None
    text = str(value).strip().split()[0] if str(value).strip() else ""
    if not text:
        return None

    match = re.match(r"^([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)([a-zA-Z%]*)$", text)
    if not match:
        return _first_svg_number(text)

    number = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "em":
        return number * font_size
    if unit == "%":
        return (number / 100.0) * font_size

    # Para mm/px/sem unidade, mantém o número no sistema de coordenadas do SVG.
    return number


def _sanitize_text_node_for_lightburn(element: ET.Element) -> None:
    if _tag_name(element) not in {"text", "tspan"}:
        return

    for attr_name in list(element.attrib.keys()):
        if attr_name in TEXT_ATTRS_TO_REMOVE_FOR_LIGHTBURN:
            element.attrib.pop(attr_name, None)

    if "style" in element.attrib:
        cleaned_style = _sanitize_style_for_editing(element.attrib.get("style", ""))
        if cleaned_style:
            element.set("style", cleaned_style)
        else:
            element.attrib.pop("style", None)


def _flatten_tspan_text_for_lightburn(text_element: ET.Element) -> List[ET.Element] | None:
    """
    Troca <text><tspan ...>...</tspan></text> por vários <text> simples.
    Isso evita que o LightBurn empilhe linhas/letras quando ignora x/y/dy de tspan.
    """
    direct_tspans = [child for child in list(text_element) if _tag_name(child) == "tspan"]
    if not direct_tspans:
        return None

    parent_attrs = dict(text_element.attrib)
    parent_attrs.pop("id", None)  # evita IDs duplicados quando uma linha vira várias
    _sanitize_text_node_for_lightburn(text_element)

    current_x = parent_attrs.get("x", "0")
    current_y = parent_attrs.get("y", "0")
    current_x_num = _first_svg_number(current_x)
    current_y_num = _first_svg_number(current_y)
    base_font_size = _font_size_number(parent_attrs)

    replacements: List[ET.Element] = []

    if text_element.text and text_element.text.strip():
        base_text = ET.Element(text_element.tag, parent_attrs)
        base_text.text = text_element.text
        _sanitize_text_node_for_lightburn(base_text)
        replacements.append(base_text)

    for index, tspan in enumerate(direct_tspans, start=1):
        attrs = dict(parent_attrs)
        tspan_attrs = dict(tspan.attrib)

        parent_transform = attrs.get("transform")
        tspan_transform = tspan_attrs.get("transform")
        attrs.update(tspan_attrs)
        if parent_transform and tspan_transform:
            attrs["transform"] = f"{parent_transform} {tspan_transform}"

        font_size = _font_size_number(attrs, base_font_size)

        if tspan_attrs.get("x") is not None:
            current_x = tspan_attrs.get("x", current_x)
            current_x_num = _first_svg_number(current_x)
        elif tspan_attrs.get("dx") is not None and current_x_num is not None:
            delta_x = _parse_svg_delta(tspan_attrs.get("dx"), font_size)
            if delta_x is not None:
                current_x_num += delta_x
                current_x = _fmt(current_x_num)

        if tspan_attrs.get("y") is not None:
            current_y = tspan_attrs.get("y", current_y)
            current_y_num = _first_svg_number(current_y)
        elif tspan_attrs.get("dy") is not None and current_y_num is not None:
            delta_y = _parse_svg_delta(tspan_attrs.get("dy"), font_size)
            if delta_y is not None:
                current_y_num += delta_y
                current_y = _fmt(current_y_num)

        attrs["x"] = str(current_x)
        attrs["y"] = str(current_y)
        attrs.pop("dx", None)
        attrs.pop("dy", None)

        # Se o tspan tinha ID próprio, preserva; se não, não duplica o ID do <text>.
        if "id" in parent_attrs and "id" not in tspan_attrs:
            attrs.pop("id", None)

        new_text = ET.Element(text_element.tag, attrs)
        new_text.text = "".join(tspan.itertext())
        new_text.tail = tspan.tail
        _sanitize_text_node_for_lightburn(new_text)
        replacements.append(new_text)

    if replacements:
        replacements[-1].tail = text_element.tail

    return replacements


def _normalize_text_children_for_lightburn(parent: ET.Element) -> None:
    for child in list(parent):
        _normalize_text_children_for_lightburn(child)

    children = list(parent)
    for child in children:
        if _tag_name(child) != "text":
            continue

        _sanitize_text_node_for_lightburn(child)
        replacements = _flatten_tspan_text_for_lightburn(child)
        if not replacements:
            continue

        insert_at = list(parent).index(child)
        parent.remove(child)
        for offset, replacement in enumerate(replacements):
            parent.insert(insert_at + offset, replacement)


def _unlock_svg_tree(element: ET.Element) -> None:
    """
    Garante que nada do SVG final fique bloqueado para seleção/edição.
    Remove/neutraliza atributos comuns de bloqueio tanto no wrapper quanto nos filhos importados.
    """
    attrs_to_remove = [
        "data-locked",
        "data-lock",
        "locked",
        "lock",
        "readonly",
        "readOnly",
        "contenteditable",
        "contentEditable",
    ]

    for attr_name in attrs_to_remove:
        if attr_name in element.attrib:
            element.attrib.pop(attr_name, None)

    if "pointer-events" in element.attrib:
        element.set("pointer-events", "all")

    if "style" in element.attrib:
        cleaned_style = _sanitize_style_for_editing(element.attrib.get("style", ""))
        if cleaned_style:
            element.set("style", cleaned_style)
        else:
            element.attrib.pop("style", None)

    element.set("data-locked", "false")
    _sanitize_text_node_for_lightburn(element)

    for child in list(element):
        _unlock_svg_tree(child)

    _normalize_text_children_for_lightburn(element)


def _load_barra_svg():
    global _barra_svg_cache
    if _barra_svg_cache is not None:
        return _barra_svg_cache

    if not os.path.exists(BARRA_SVG_PATH):
        print(f"[WARN] Barra.svg não encontrado em '{BARRA_SVG_PATH}'. Separadores SVG não serão inseridos.")
        _barra_svg_cache = None
        return None

    try:
        _barra_svg_cache = _load_svg_root(
            BARRA_SVG_PATH,
            BARRA_DXF_FIXED_WIDTH_MM,
            BARRA_DXF_FIXED_HEIGHT_MM,
        )
        print(
            f"[INFO] Barra.svg carregado. Dimensões: "
            f"{_barra_svg_cache['width']:.2f}x{_barra_svg_cache['height']:.2f} mm"
        )
        return _barra_svg_cache
    except Exception as exc:
        print(f"[ERROR] Erro ao carregar Barra.svg: {exc}. Separadores SVG não serão inseridos.")
        _barra_svg_cache = None
        return None


def _add_barra_if_available(parent_group: ET.Element, barra_svg, current_x_pos: float, row_base_y: float) -> float:
    if not barra_svg:
        return current_x_pos + ESPACAMENTO_DXF_MESMO_FURO

    current_x_pos += ESPACAMENTO_SEPARADOR_PRE_BARRA
    parent_group.append(_prepare_nested_svg(barra_svg, current_x_pos, row_base_y))
    current_x_pos += barra_svg["width"] + ESPACAMENTO_SEPARADOR_POST_BARRA + ADJUSTMENT_OFFSET_BARRA_MM
    return current_x_pos



def _add_plan_title(parent_group: ET.Element, plan_name: str) -> None:
    """
    Adiciona o título do plano como <text> simples, sem baseline avançado.
    O LightBurn é mais consistente quando recebe x/y absolutos e y como baseline.
    """
    plano_text = ET.SubElement(parent_group, f"{{{SVG_NS}}}text")
    plano_text.set("id", f"plano-gravacao-{re.sub(r'[^A-Za-z0-9_-]+', '-', str(plan_name)).strip('-') or 'sem-nome'}")
    plano_text.set("x", f"{_fmt(PLANO_TEXTO_X_MM)}")
    plano_text.set("y", f"{_fmt(PLANO_TEXTO_Y_MM + PLANO_TEXTO_TAMANHO_FONTE_MM)}")
    plano_text.set("font-family", "Arial, Helvetica, sans-serif")
    plano_text.set("font-size", f"{_fmt(PLANO_TEXTO_TAMANHO_FONTE_MM)}")
    plano_text.set("font-weight", "900")
    plano_text.set("fill", "#ff0000")
    plano_text.set("text-anchor", "start")
    plano_text.set("pointer-events", "all")
    plano_text.set("data-locked", "false")
    plano_text.text = f"Plano de Gravação {plan_name}"

def generate_single_plan_svg_layout_data(
    file_ids_and_skus: list[dict],
    plan_name: str,
    drive_folder_id: str,
) -> Tuple[ET.Element, float, float, List[str]]:
    """
    Gera um grupo SVG para um único plano de gravação.
    O layout segue a mesma lógica do DXF: cor > formato > tamanho > furo,
    com Barra.svg como separador quando existir.
    """
    print(f"DEBUG: generate_single_plan_svg_layout_data() - Chamado para plano '{plan_name}'.")

    barra_svg = _load_barra_svg()
    organized_svgs = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    failed_ids_current_plan: List[str] = []

    for item_data in file_ids_and_skus:
        target_id_from_sheet = item_data["id_arquivo_drive"]
        sku = item_data["sku"]

        dxf_format, dxf_size, hole_type, color_code = parse_sku(sku)
        if not dxf_format or not dxf_size or not hole_type or not color_code:
            print(f"[WARN] SKU '{sku}' (ID: {target_id_from_sheet}) inválido ou incompleto.")
            failed_ids_current_plan.append(target_id_from_sheet)
            continue

        try:
            real_file_id = item_data.get("arquivo_drive_id_resolvido")
            nome_arquivo_drive = item_data.get("nome_arquivo_drive")

            if not real_file_id or not nome_arquivo_drive:
                real_file_id, nome_arquivo_drive = buscar_arquivo_personalizado_por_id_e_sku(
                    target_id=target_id_from_sheet,
                    sku=sku,
                    drive_folder_id=drive_folder_id,
                    extensoes_permitidas=(".svg",),
                )

            local_svg_name = f"{sku}.svg"
            svg_path_local = baixar_arquivo_drive(real_file_id, local_svg_name, drive_folder_id)

            try:
                svg_data = _load_svg_root(svg_path_local, ITEM_DXF_FIXED_WIDTH_MM, ITEM_DXF_FIXED_HEIGHT_MM)
            finally:
                if os.path.exists(svg_path_local):
                    os.remove(svg_path_local)

            organized_svgs[color_code][dxf_format][dxf_size][hole_type].append({
                "svg_data": svg_data,
                "sku": sku,
                "bbox_width": svg_data["width"],
                "bbox_height": svg_data["height"],
                "original_min_x": svg_data["original_min_x"],
                "original_min_y": svg_data["original_min_y"],
                "id_arquivo_drive": target_id_from_sheet,
            })
            print(
                f"[INFO] SVG para SKU '{sku}' (ID: {target_id_from_sheet}) processado. "
                f"Dimensões: {svg_data['width']:.2f}x{svg_data['height']:.2f} mm"
            )

        except Exception as exc:
            print(f"[ERROR] Erro ao processar SVG do ID '{target_id_from_sheet}' / SKU '{sku}': {exc}")
            failed_ids_current_plan.append(target_id_from_sheet)
            continue

    if not organized_svgs:
        raise NoEntitiesFoundError(f"Nenhum SVG válido para o layout do plano '{plan_name}'.")

    plan_group = ET.Element(f"{{{SVG_NS}}}g")
    plan_group.set("data-plan-name", str(plan_name))

    text_width_estimate = max(PLANO_DXF_FIXED_WIDTH_MM, len(f"Plano de Gravação {plan_name}") * 9.0)
    layout_width = MARGEM_ESQUERDA + text_width_estimate + MARGEM_ESQUERDA

    _add_plan_title(plan_group, plan_name)

    # Espaço reservado fixo para o título. Isso impede que a posição dos itens dependa
    # da altura calculada da fonte em cada editor/visualizador SVG.
    current_y = MARGEM_SUPERIOR + PLANO_TEXTO_CAIXA_ALTURA_MM + ESPACAMENTO_PLANO_COR

    for color_code in sorted(organized_svgs.keys()):
        color_group = organized_svgs[color_code]
        current_x_pos = MARGEM_ESQUERDA
        max_height_in_color_line = 0.0

        if barra_svg:
            max_height_in_color_line = max(max_height_in_color_line, barra_svg["height"])

        for dxf_format in sorted(color_group.keys()):
            for dxf_size in sorted(color_group[dxf_format].keys()):
                for hole_type in sorted(color_group[dxf_format][dxf_size].keys()):
                    for svg_item in color_group[dxf_format][dxf_size][hole_type]:
                        max_height_in_color_line = max(max_height_in_color_line, svg_item["bbox_height"])

        row_base_y = current_y

        first_format_in_line = True
        for dxf_format in sorted(color_group.keys()):
            if not first_format_in_line:
                current_x_pos = _add_barra_if_available(plan_group, barra_svg, current_x_pos, row_base_y)

            first_size_in_format = True
            for dxf_size in sorted(color_group[dxf_format].keys()):
                if not first_size_in_format:
                    current_x_pos = _add_barra_if_available(plan_group, barra_svg, current_x_pos, row_base_y)

                first_hole_type_in_size = True
                for hole_type in sorted(color_group[dxf_format][dxf_size].keys()):
                    if not first_hole_type_in_size:
                        current_x_pos = _add_barra_if_available(plan_group, barra_svg, current_x_pos, row_base_y)

                    sorted_items = sorted(color_group[dxf_format][dxf_size][hole_type], key=lambda x: x["sku"])
                    first_svg_in_group = True
                    for svg_item in sorted_items:
                        if not first_svg_in_group:
                            current_x_pos += ESPACAMENTO_DXF_MESMO_FURO

                        plan_group.append(_prepare_nested_svg(svg_item["svg_data"], current_x_pos, row_base_y))

                        current_x_pos += svg_item["bbox_width"]
                        layout_width = max(layout_width, current_x_pos + MARGEM_ESQUERDA)
                        first_svg_in_group = False

                    first_hole_type_in_size = False
                first_size_in_format = False
            first_format_in_line = False

        current_y += max_height_in_color_line + ESPACAMENTO_LINHA_COR

    layout_height = current_y - ESPACAMENTO_LINHA_COR + MARGEM_INFERIOR
    if layout_height <= 0:
        layout_height = MARGEM_SUPERIOR + PLANO_TEXTO_ALTURA_MM + MARGEM_INFERIOR

    print(f"[INFO] Layout SVG do plano '{plan_name}' gerado. Dimensões: {layout_width:.2f}x{layout_height:.2f} mm")
    return plan_group, layout_width, layout_height, failed_ids_current_plan


def create_svg_document(width_mm: float, height_mm: float) -> ET.Element:
    root = ET.Element(f"{{{SVG_NS}}}svg")
    root.set("width", f"{_fmt(width_mm)}mm")
    root.set("height", f"{_fmt(height_mm)}mm")
    root.set("viewBox", f"0 0 {_fmt(width_mm)} {_fmt(height_mm)}")
    root.set("version", "1.1")
    root.set("pointer-events", "all")
    root.set("data-locked", "false")
    return root


def save_svg_document(root: ET.Element, output_path: str) -> None:
    _unlock_svg_tree(root)
    tree = ET.ElementTree(root)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
