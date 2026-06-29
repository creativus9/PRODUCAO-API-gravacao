import os
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

def carregar_geometria(filepath):
    """
    Lê um arquivo DXF ou SVG e retorna o objeto doc e o modelspace preenchido.
    Para SVG, requer a biblioteca 'svgelements' instalada (pip install svgelements).
    """
    ext = os.path.splitext(filepath)[1].lower()
    
    if ext == '.svg':
        try:
            from svgelements import SVG, Path, Shape
        except ImportError:
            raise ImportError("O pacote 'svgelements' não está instalado. Adicione no requirements.txt ou execute: pip install svgelements")
        
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        try:
            svg = SVG.parse(filepath)
            
            # Extrai geometria do SVG e converte para polilinhas DXF
            for element in svg.elements():
                # Precisamos aplicar o transform (matrix) que vem no SVG para que fique no tamanho/posição certa
                transform_matrix = element.transform if hasattr(element, 'transform') else None
                
                try:
                    if isinstance(element, Shape):
                        element = Path(element) # Converte formas básicas (rect, circle) para caminhos matemáticos
                        
                    if isinstance(element, Path):
                        # Pega o comprimento total do Path. svgelements agrupa os subpaths no objeto Path pai
                        total_length = element.length()
                        
                        if total_length == 0:
                            continue
                            
                        # Iterar sobre os subpaths. Em svgelements, as_subpaths() retorna novos objetos Path
                        for subpath in element.as_subpaths():
                            subpath_length = subpath.length()
                            if subpath_length == 0:
                                continue
                                
                            # Amostragem adaptativa (mais pontos em caminhos longos/curvos)
                            num_samples = max(20, int(subpath_length)) 
                            sub_points = []
                            
                            for i in range(num_samples + 1):
                                # point() retorna o ponto ao longo da curva baseada em uma proporção de 0 a 1
                                pt = subpath.point(i / num_samples)
                                
                                x, y = pt.x, pt.y
                                
                                # Se houver matriz de transformação aplicada ao grupo/elemento, multiplica as coordenadas
                                if transform_matrix:
                                    x_trans = transform_matrix.a * x + transform_matrix.c * y + transform_matrix.e
                                    y_trans = transform_matrix.b * x + transform_matrix.d * y + transform_matrix.f
                                    x, y = x_trans, y_trans
                                
                                # SVG cresce o eixo Y para baixo, DXF cresce o Y para cima. Invertemos o Y:
                                sub_points.append((x, -y))
                                
                            if sub_points:
                                msp.add_lwpolyline(sub_points)
                except Exception as e:
                    print(f"[WARN] Erro ao converter elemento SVG '{type(element).__name__}': {e}")
                    
        except Exception as file_err:
             print(f"[ERROR] Falha grave ao tentar ler o arquivo SVG '{filepath}': {file_err}")
             # Em caso de erro crítico no SVG, retornaremos um DXF vazio para o layout engine tentar seguir
             pass

        return doc, msp

    elif ext == '.dxf':
        doc = ezdxf.readfile(filepath)
        return doc, doc.modelspace()
    
    else:
        raise ValueError(f"Formato não suportado: {ext}")