import os
import ezdxf
from ezdxf.math import BoundingBox, Vec3

def parse_sku(sku: str):
    """
    Analisa a string SKU e extrai as informações relevantes.
    """
    parts = sku.split('-')
    if len(parts) != 7:
        print(f"[WARN] SKU '{sku}' não está no formato esperado (7 grupos).")
        return None, None, None, None

    dxf_format = parts[0]
    dxf_size = parts[1]
    hole_type = parts[2]
    color_code = parts[4]

    return dxf_format, dxf_size, hole_type, color_code

def calcular_bbox_dxf(msp):
    """
    Calcula o bounding box de todas as entidades no modelspace.
    """
    bbox_union = BoundingBox()
    found_any_entity = False

    for e in msp:
        found_any_entity = True
        try:
            entity_bbox = e.bbox()
            if entity_bbox.is_empty:
                continue
            bbox_union.extend(entity_bbox.extmin)
            bbox_union.extend(entity_bbox.extmax)
        except Exception:
            pass

    if not found_any_entity or bbox_union.is_empty:
        return 0, 0, 0, 0

    min_x, min_y = bbox_union.extmin.x, bbox_union.extmin.y
    max_x, max_y = bbox_union.extmax.x, bbox_union.extmax.y

    if min_x >= max_x or min_y >= max_y:
        return 0, 0, 0, 0

    return min_x, min_y, max_x, max_y

def carregar_geometria(filepath):
    """
    Lê um arquivo DXF ou SVG e retorna o objeto doc e o modelspace.
    """
    ext = os.path.splitext(filepath)[1].lower()
    
    if ext == '.svg':
        try:
            from svgelements import SVG, Path, Shape
        except ImportError:
            raise ImportError("O pacote 'svgelements' não está instalado.")
        
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        try:
            svg = SVG.parse(filepath)
            for element in svg.elements():
                transform_matrix = element.transform if hasattr(element, 'transform') else None
                
                try:
                    if isinstance(element, Shape):
                        element = Path(element)
                        
                    if isinstance(element, Path):
                        # Processa cada subcaminho individualmente para evitar o erro de atribuição
                        for subpath in element.as_subpaths():
                            sub_p = Path(subpath)
                            
                            try:
                                subpath_length = sub_p.length()
                            except (TypeError, AttributeError):
                                subpath_length = 0

                            if subpath_length == 0:
                                continue
                                
                            num_samples = max(20, int(subpath_length)) 
                            sub_points = []
                            
                            for i in range(num_samples + 1):
                                pt = sub_p.point(i / num_samples)
                                x, y = pt.x, pt.y
                                
                                if transform_matrix:
                                    x_trans = transform_matrix.a * x + transform_matrix.c * y + transform_matrix.e
                                    y_trans = transform_matrix.b * x + transform_matrix.d * y + transform_matrix.f
                                    x, y = x_trans, y_trans
                                
                                # Inverte o eixo Y para o padrão DXF
                                sub_points.append((x, -y))
                                
                            if sub_points:
                                msp.add_lwpolyline(sub_points)
                except Exception as e:
                    print(f"[WARN] Erro ao converter elemento SVG: {e}")
                    
        except Exception as file_err:
             print(f"[ERROR] Falha grave ao ler SVG: {file_err}")
             pass

        return doc, msp

    elif ext == '.dxf':
        doc = ezdxf.readfile(filepath)
        return doc, doc.modelspace()
    
    else:
        raise ValueError(f"Formato não suportado: {ext}")