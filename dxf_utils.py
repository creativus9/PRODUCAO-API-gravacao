import os
import ezdxf
from ezdxf.math import BoundingBox, Vec3

def parse_sku(sku: str):
    """
    Analisa a string SKU e extrai as informações relevantes.
    """
    parts = sku.split('-')
    if len(parts) != 7:
        print(f"[WARN] SKU '{sku}' não está no formato esperado.")
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
            if not entity_bbox.is_empty:
                bbox_union.extend(entity_bbox.extmin)
                bbox_union.extend(entity_bbox.extmax)
        except Exception:
            pass

    if not found_any_entity or bbox_union.is_empty:
        return 0, 0, 0, 0

    return bbox_union.extmin.x, bbox_union.extmin.y, bbox_union.extmax.x, bbox_union.extmax.y

def carregar_geometria(filepath):
    """
    Lê um arquivo DXF ou SVG com salvaguardas contra travamentos.
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
            # Limite de segurança para evitar loops infinitos em arquivos corrompidos
            element_count = 0
            for element in svg.elements():
                element_count += 1
                if element_count > 1000:
                    print(f"[WARN] SVG complexo demais, limitando processamento em {filepath}")
                    break

                transform_matrix = element.transform if hasattr(element, 'transform') else None
                
                try:
                    if isinstance(element, Shape):
                        element = Path(element)
                        
                    if isinstance(element, Path):
                        for subpath in element.as_subpaths():
                            sub_p = Path(subpath)
                            
                            # Validação preventiva de comprimento
                            try:
                                length = float(sub_p.length())
                            except:
                                length = 0
                            
                            if length <= 0:
                                continue
                                
                            # Amostragem otimizada: nem muito baixa para perder qualidade, nem alta para travar
                            num_samples = min(max(10, int(length / 2)), 100) 
                            sub_points = []
                            
                            for i in range(num_samples + 1):
                                pt = sub_p.point(i / num_samples)
                                x, y = pt.x, pt.y
                                
                                if transform_matrix:
                                    x_trans = transform_matrix.a * x + transform_matrix.c * y + transform_matrix.e
                                    y_trans = transform_matrix.b * x + transform_matrix.d * y + transform_matrix.f
                                    x, y = x_trans, y_trans
                                
                                sub_points.append((x, -y))
                                
                            if len(sub_points) > 1:
                                msp.add_lwpolyline(sub_points)
                except Exception as e:
                    print(f"[WARN] Elemento ignorado devido a erro: {e}")
                    
        except Exception as file_err:
             print(f"[ERROR] Erro ao processar SVG: {file_err}")

        return doc, msp

    elif ext == '.dxf':
        return ezdxf.readfile(filepath), None
    
    else:
        raise ValueError(f"Formato não suportado: {ext}")