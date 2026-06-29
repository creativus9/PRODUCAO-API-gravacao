import ezdxf
from ezdxf.math import BoundingBox, Vec3 # Adiciona importação de BoundingBox e Vec3

def parse_sku(sku: str):
    """
    Analisa a string SKU e extrai as informações relevantes.
    Exemplo: PLAC-3010-2FH-AC-DOU-070-00000
    Grupos: 1-formato, 2-tamanho, 3-furo, 4-material, 5-cor, 6-quantidade, 7-estilo da arte
    """
    parts = sku.split('-')
    if len(parts) != 7:
        print(f"[WARN] SKU '{sku}' não está no formato esperado (7 grupos).")
        return None, None, None, None # Retorna None para todos os valores se o formato não for o esperado

    # Extrai os novos campos
    dxf_format = parts[0] # Grupo 1: formato
    dxf_size = parts[1]   # Grupo 2: tamanho
    hole_type = parts[2]  # Grupo 3: tipo de furo
    color_code = parts[4] # Grupo 5: código da cor

    return dxf_format, dxf_size, hole_type, color_code

def calcular_bbox_dxf(msp):
    """
    Calcula o bounding box (caixa delimitadora) de todas as entidades no modelspace de um DXF.
    Retorna (min_x, min_y, max_x, max_y).
    Esta versão itera sobre as entidades e usa BoundingBox para maior robustez.
    """
    bbox_union = BoundingBox() # Inicializa uma caixa delimitadora vazia
    found_any_entity = False # Flag para verificar se alguma entidade foi processada

    for e in msp:
        found_any_entity = True # Encontrou pelo menos uma entidade
        try:
            # Tenta obter a caixa delimitadora da entidade
            entity_bbox = e.bbox()
            
            if entity_bbox.is_empty:
                # Se a bbox da entidade for vazia, pula para a próxima
                continue

            # Adiciona os pontos extremos da bbox da entidade à bbox de união
            bbox_union.extend(entity_bbox.extmin)
            bbox_union.extend(entity_bbox.extmax)

        except Exception as err:
            # Ignora entidades que causam erro no cálculo da bbox
            pass

    if not found_any_entity:
        print(f"[WARN] Nenhuma entidade encontrada no modelspace para calcular bbox. Retornando 0,0,0,0.")
        return 0, 0, 0, 0

    if bbox_union.is_empty:
        print(f"[WARN] Bounding box união está vazio (provavelmente todas as entidades tinham bbox vazio ou erro). Retornando 0,0,0,0.")
        return 0, 0, 0, 0

    # Extrai as coordenadas da caixa delimitadora de união
    min_x, min_y = bbox_union.extmin.x, bbox_union.extmin.y
    max_x, max_y = bbox_union.extmax.x, bbox_union.extmax.y

    # Validação básica: se min_x/y for maior ou igual a max_x/y, significa que não há extensão de geometria válida
    if min_x >= max_x or min_y >= max_y:
        print(f"[WARN] Bounding box calculado é inválido (min >= max). Retornando 0,0,0,0. (min_x={min_x}, max_x={max_x}, min_y={min_y}, max_y={max_y})")
        return 0, 0, 0, 0

    return min_x, min_y, max_x, max_y
