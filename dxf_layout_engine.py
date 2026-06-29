# dxf_layout_engine.py
import os
import ezdxf
import re
import datetime
from collections import defaultdict
from typing import Optional, List, Tuple, Any

# Adicionando prints para depuração
print("DEBUG: dxf_layout_engine.py - Início do carregamento do módulo.")

# Importa as funções utilitárias e de Google Drive
try:
    from dxf_utils import parse_sku, calcular_bbox_dxf
    print("DEBUG: dxf_layout_engine.py - dxf_utils importado com sucesso.")
except ImportError as e:
    print(f"ERROR: dxf_layout_engine.py - Falha ao importar dxf_utils: {e}")
    raise # Re-levanta o erro para que o traceback seja claro

try:
    from google_drive_utils import baixar_arquivo_drive, upload_to_drive, buscar_arquivo_personalizado_por_id_e_sku
    print("DEBUG: dxf_layout_engine.py - google_drive_utils importado com sucesso.")
except ImportError as e:
    print(f"ERROR: dxf_layout_engine.py - Falha ao importar google_drive_utils: {e}")
    raise # Re-levanta o erro para que o traceback seja claro

# --- Exceção Customizada ---
# Esta exceção agora será levantada apenas se NENHUMA entidade válida for gerada para um plano.
# Falhas em itens individuais serão reportadas na lista de retorno.
class NoEntitiesFoundError(Exception):
    """Exceção levantada quando nenhum DXF válido é encontrado para gerar o layout de um plano."""
    pass
# --- Fim da Exceção Customizada ---

# --- Configurações de Layout (em mm) ---
# Tamanho da folha de corte (exemplo: A0 ou um tamanho personalizado)
FOLHA_LARGURA_MM, FOLHA_ALTURA_MM = 1200, 900 # Exemplo: 1.2m x 0.9m

# Espaçamentos
ESPACAMENTO_DXF_MESMO_FURO = 100.0  # Espaçamento horizontal entre DXFs do mesmo tipo de furo
ESPACAMENTO_LINHA_COR = 200.0       # Espaçamento vertical entre linhas de cores diferentes
ESPACAMENTO_PLANO_COR = 100.0       # Espaçamento vertical entre o DXF do plano e a primeira linha de cor

# Margens da folha
MARGEM_ESQUERDA = 50
MARGEM_SUPERIOR = 50
MARGEM_INFERIOR = 50

# --- Dimensões Fixas para Fallback (Adicionado) ---
# Usadas se calcular_bbox_dxf retornar 0x0
PLANO_DXF_FIXED_WIDTH_MM = 236.0
PLANO_DXF_FIXED_HEIGHT_MM = 21.5

ITEM_DXF_FIXED_WIDTH_MM = 129.0
ITEM_DXF_FIXED_HEIGHT_MM = 225.998

# --- Configurações da Barra Separadora ---
BARRA_DXF_PATH = os.path.join("Plano_Info", "Barra.dxf")
BARRA_DXF_FIXED_WIDTH_MM = 10.0  # Largura da barra
BARRA_DXF_FIXED_HEIGHT_MM = 250.0 # Altura da barra

# Novos espaçamentos para a barra
ESPACAMENTO_SEPARADOR_PRE_BARRA = 100.0 # Espaçamento antes da barra
ESPACAMENTO_SEPARADOR_POST_BARRA = 100.0 # Espaçamento depois da barra

# Variável de ajuste manual para a distância da barra (pode ser positiva ou negativa)
# Use esta variável para corrigir pequenas variações na distância percebida da barra.
ADJUSTMENT_OFFSET_BARRA_MM = 250.0

# Variável global para armazenar as entidades da barra
barra_entities = []
barra_width = BARRA_DXF_FIXED_WIDTH_MM
barra_height = BARRA_DXF_FIXED_HEIGHT_MM
barra_original_min_x, barra_original_min_y = 0.0, 0.0

print("DEBUG: dxf_layout_engine.py - Variáveis globais e constantes definidas.")

def load_barra_dxf():
    """Carrega as entidades do Barra.dxf uma vez."""
    global barra_entities, barra_width, barra_height, barra_original_min_x, barra_original_min_y
    print("DEBUG: load_barra_dxf() - Chamado.")
    if not barra_entities: # Carrega apenas se ainda não foi carregado
        print(f"DEBUG: load_barra_dxf() - Barra DXF não carregada, tentando carregar de '{BARRA_DXF_PATH}'.")
        if os.path.exists(BARRA_DXF_PATH):
            try:
                barra_doc = ezdxf.readfile(BARRA_DXF_PATH)
                barra_msp = barra_doc.modelspace()
                
                min_x_barra, min_y_barra, max_x_barra, max_y_barra = calcular_bbox_dxf(barra_msp)
                
                # Fallback para dimensões fixas se bbox for 0x0
                if (max_x_barra - min_x_barra) == 0.0 and (max_y_barra - min_y_barra) == 0.0:
                    print(f"[WARN] Dimensões de Barra.dxf calculadas como 0x0. Usando dimensões fixas: {BARRA_DXF_FIXED_WIDTH_MM}x{BARRA_DXF_FIXED_HEIGHT_MM} mm.")
                    barra_width = BARRA_DXF_FIXED_WIDTH_MM
                    barra_height = BARRA_DXF_FIXED_HEIGHT_MM
                    barra_original_min_x, barra_original_min_y = 0.0, 0.0
                else:
                    barra_width = max_x_barra - min_x_barra
                    barra_height = max_y_barra - min_y_barra
                    barra_original_min_x, barra_original_min_y = min_x_barra, min_y_barra

                for ent in barra_msp:
                    barra_entities.append(ent.copy())
                print(f"[INFO] Barra.dxf carregado. Dimensões: {barra_width:.2f}x{barra_height:.2f} mm")
            except ezdxf.DXFStructureError as e:
                print(f"[ERROR] Arquivo DXF '{BARRA_DXF_PATH}' corrompido ou inválido: {e}")
                barra_entities = []
            except Exception as e:
                print(f"[ERROR] Erro ao carregar DXF '{BARRA_DXF_PATH}': {e}")
                barra_entities = []
        else:
            print(f"[WARN] Barra.dxf não encontrado em '{BARRA_DXF_PATH}'. Separadores não serão inseridos.")
    else:
        print("DEBUG: load_barra_dxf() - Barra DXF já carregada.")

print("DEBUG: dxf_layout_engine.py - load_barra_dxf() definida.")

def generate_single_plan_layout_data(
    file_ids_and_skus: list[dict],
    plan_name: str,
    drive_folder_id: str,
) -> Tuple[List[Tuple[Any, float, float]], float, float, List[str]]: # Retorna List[str] para failed_ids
    """
    Gera as entidades DXF e suas posições relativas para o layout de um único plano de corte,
    assumindo que o canto inferior esquerdo do layout final será (0,0).

    Args:
        file_ids_and_skus: Lista de dicionários, cada um com 'id_arquivo_drive' (ID lógico do nome) do Drive
                           e 'sku' correspondente.
        plan_name: Nome do plano de corte (ex: "01", "A").
        drive_folder_id: ID da pasta principal do Google Drive.

    Returns:
        Uma tupla contendo:
        - Uma lista de tuplas: (entidade ezdxf copiada, x_pos_relativa, y_pos_relativa)
        - A largura total do layout do plano.
        - A altura total do layout do plano.
        - Uma lista dos 'id_arquivo_drive' que falharam ao serem processados.
    """
    print(f"DEBUG: generate_single_plan_layout_data() - Chamado para plano '{plan_name}'.")
    
    # Carrega a barra DXF se ainda não foi carregada
    load_barra_dxf()

    # Usaremos um documento temporário para calcular as posições relativas
    # e depois copiaremos as entidades para o documento principal no main.py
    temp_doc = ezdxf.new('R2010')
    temp_doc.header['$INSUNITS'] = 4  # Define a unidade como Milímetros (4)
    temp_msp = temp_doc.modelspace()

    # Estrutura para organizar os DXFs por cor, formato, tamanho e furo
    # { 'DOU': { 'PLAC': { '3010': { '2FH': [ {dxf_entity, original_sku, bbox_width, bbox_height, original_min_x, original_min_y}, ... ] } } } }
    organized_dxfs = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    
    failed_ids_current_plan = [] # Nova lista para IDs de arquivos que falharam neste plano

    # --- 1. Baixar e Organizar DXFs de Itens ---
    print(f"[INFO] Baixando e organizando DXFs de itens para o plano '{plan_name}'...")
    for item_data in file_ids_and_skus:
        target_id_from_sheet = item_data['id_arquivo_drive'] 
        sku = item_data['sku']
        
        print(f"DEBUG: Processing SKU: {sku} (ID: {target_id_from_sheet})")
        dxf_format, dxf_size, hole_type, color_code = parse_sku(sku)
        print(f"DEBUG: parse_sku returned: format={dxf_format}, size={dxf_size}, hole={hole_type}, color={color_code}")

        if not dxf_format or not dxf_size or not hole_type or not color_code:
            print(f"[WARN] SKU '{sku}' (ID: {target_id_from_sheet}) inválido ou incompleto, ignorando item e adicionando ID a falhas.")
            failed_ids_current_plan.append(target_id_from_sheet) # Adiciona ID à lista de falhas
            continue

        try:
            print(f"DEBUG: Calling buscar_arquivo_personalizado_por_id_e_sku for target_id={target_id_from_sheet}, sku={sku}")
            result_from_search = buscar_arquivo_personalizado_por_id_e_sku(
                target_id=target_id_from_sheet,
                sku=sku,
                drive_folder_id=drive_folder_id
            )
            print(f"DEBUG: buscar_arquivo_personalizado_por_id_e_sku returned: {result_from_search}")
            real_file_id, nome_arquivo_drive = result_from_search
            print(f"[INFO] Arquivo encontrado no Drive: ID real='{real_file_id}', Nome='{nome_arquivo_drive}'")
        except FileNotFoundError as e:
            print(f"[ERROR] Falha ao encontrar arquivo no Drive para ID lógico '{target_id_from_sheet}' e SKU '{sku}': {e}. Adicionando ID a falhas.")
            failed_ids_current_plan.append(target_id_from_sheet) # Adiciona ID à lista de falhas
            continue
        except Exception as e:
            print(f"[ERROR] Erro inesperado ao buscar arquivo no Drive para ID lógico '{target_id_from_sheet}' e SKU '{sku}': {e}. Adicionando ID a falhas.")
            failed_ids_current_plan.append(target_id_from_sheet) # Adiciona ID à lista de falhas
            continue

        local_dxf_name = f"{sku}.dxf"
        try:
            dxf_path_local = baixar_arquivo_drive(real_file_id, local_dxf_name, drive_folder_id)
        except Exception as e:
            print(f"[ERROR] Falha ao baixar DXF para SKU '{sku}' (ID real: {real_file_id}): {e}. Adicionando ID a falhas.")
            failed_ids_current_plan.append(target_id_from_sheet) # Adiciona ID à lista de falhas
            continue

        try:
            item_doc = ezdxf.readfile(dxf_path_local)
            item_msp = item_doc.modelspace()
            min_x, min_y, max_x, max_y = calcular_bbox_dxf(item_msp)
            
            dxf_width = max_x - min_x
            dxf_height = max_y - min_y

            # --- Fallback para dimensões fixas se bbox for 0x0 (Adicionado) ---
            if dxf_width == 0.0 and dxf_height == 0.0:
                print(f"[WARN] Dimensões de SKU '{sku}' (ID: {target_id_from_sheet}) calculadas como 0x0. Usando dimensões fixas: {ITEM_DXF_FIXED_WIDTH_MM}x{ITEM_DXF_FIXED_HEIGHT_MM} mm.")
                dxf_width = ITEM_DXF_FIXED_WIDTH_MM
                dxf_height = ITEM_DXF_FIXED_HEIGHT_MM
                # Para o offset, assumimos que o ponto de origem do desenho é (0,0) se não houver bbox válido
                min_x, min_y = 0.0, 0.0 
            # --- Fim do Fallback ---

            entities_to_add = []
            for entity in item_msp:
                entities_to_add.append(entity.copy()) # Copia para evitar referências ao doc original

            if not entities_to_add: # Se o DXF foi lido mas não tem entidades visíveis
                print(f"[WARN] DXF para SKU '{sku}' (ID: {target_id_from_sheet}) não contém entidades visíveis. Adicionando ID a falhas.")
                failed_ids_current_plan.append(target_id_from_sheet)
                continue

            organized_dxfs[color_code][dxf_format][dxf_size][hole_type].append({
                'entities': entities_to_add,
                'sku': sku,
                'bbox_width': dxf_width,
                'bbox_height': dxf_height,
                'original_min_x': min_x,
                'original_min_y': min_y,
                'id_arquivo_drive': target_id_from_sheet # Adiciona o ID aqui para rastreamento
            })
            print(f"[INFO] DXF para SKU '{sku}' (ID: {target_id_from_sheet}, cor: {color_code}, formato: {dxf_format}, tamanho: {dxf_size}, furo: {hole_type}) processado. Dimensões: {dxf_width:.2f}x{dxf_height:.2f} mm")

        except ezdxf.DXFStructureError as e:
            print(f"[ERROR] Arquivo DXF '{dxf_path_local}' corrompido ou inválido: {e}. Adicionando ID a falhas.")
            failed_ids_current_plan.append(target_id_from_sheet) # Adiciona ID à lista de falhas
        except Exception as e:
            print(f"[ERROR] Erro ao processar DXF '{dxf_path_local}': {e}. Adicionando ID a falhas.")
            failed_ids_current_plan.append(target_id_from_sheet) # Adiciona ID à lista de falhas
        finally:
            if os.path.exists(dxf_path_local):
                os.remove(dxf_path_local)

    # --- 2. Preparar DXF do Plano de Corte ---
    plano_info_dxf_path = os.path.join("Plano_Info", f"{plan_name}.dxf")
    
    plano_width = 0
    plano_height = 0
    plano_entities = [] # Lista para armazenar as entidades do plano
    plano_original_min_x, plano_original_min_y = 0.0, 0.0

    if os.path.exists(plano_info_dxf_path):
        try:
            plano_doc = ezdxf.readfile(plano_info_dxf_path)
            plano_msp = plano_doc.modelspace()
            
            min_x_plano, min_y_plano, max_x_plano, max_y_plano = calcular_bbox_dxf(plano_msp)
            plano_width = max_x_plano - min_x_plano
            plano_height = max_y_plano - min_y_plano
            plano_original_min_x, plano_original_min_y = min_x_plano, min_y_plano

            # --- Fallback para dimensões fixas se bbox for 0x0 (Adicionado) ---
            if plano_width == 0.0 and plano_height == 0.0:
                print(f"[WARN] Dimensões do plano '{plan_name}.dxf' calculadas como 0x0. Usando dimensões fixas: {PLANO_DXF_FIXED_WIDTH_MM}x{PLANO_DXF_FIXED_HEIGHT_MM} mm.")
                plano_width = PLANO_DXF_FIXED_WIDTH_MM
                plano_height = PLANO_DXF_FIXED_HEIGHT_MM
                plano_original_min_x, plano_original_min_y = 0.0, 0.0 # Reinicia offset se usar fixo
            # --- Fim do Fallback ---

            for ent in plano_msp:
                plano_entities.append(ent.copy()) # Copia para evitar referências ao doc original
            
            print(f"[INFO] DXF do plano de corte '{plano_info_dxf_path}' carregado. Dimensões: {plano_width:.2f}x{plano_height:.2f} mm")

        except ezdxf.DXFStructureError as e:
            print(f"[ERROR] Arquivo DXF do plano de corte '{plano_info_dxf_path}' corrompido ou inválido: {e}. Plano não será inserido.")
            plano_entities = [] # Limpa as entidades se houver erro
        except Exception as e:
            print(f"[ERROR] Erro ao carregar DXF do plano de corte '{plano_info_dxf_path}': {e}. Plano não será inserido.")
            plano_entities = [] # Limpa as entidades se houver erro
    else:
        print(f"[WARN] DXF do plano de corte '{plano_info_dxf_path}' não encontrado localmente. Não será inserido.")

    # --- 3. Posicionar e Coletar Entidades no Modelspace Relativo ---
    
    # Esta lista armazenará todas as entidades com suas posições finais RELATIVAS
    # ao canto inferior esquerdo do layout deste plano (que será (0,0) após o ajuste final)
    all_relative_entities_with_coords = []

    # Altura total estimada para o layout deste plano (primeira passagem para estimar altura)
    estimated_layout_height = 0
    if plano_entities:
        estimated_layout_height += plano_height + ESPACAMENTO_PLANO_COR
    
    # Adiciona a altura de cada linha de cor + espaçamento
    for color_code in sorted(organized_dxfs.keys()):
        color_group = organized_dxfs[color_code]
        max_height_in_color_line = 0
        
        if barra_entities: # Considera a altura da barra se ela for inserida
            max_height_in_color_line = max(max_height_in_color_line, barra_height)

        for dxf_format in sorted(color_group.keys()):
            format_group = color_group[dxf_format]
            for dxf_size in sorted(format_group.keys()):
                size_group = format_group[dxf_size]
                for hole_type in size_group.keys():
                    for dxf_item in size_group[hole_type]:
                        max_height_in_color_line = max(max_height_in_color_line, dxf_item['bbox_height'])
        
        # Se houver itens nesta linha de cor, adiciona a altura máxima e o espaçamento da linha de cor
        if max_height_in_color_line > 0:
            estimated_layout_height += max_height_in_color_line + ESPACAMENTO_LINHA_COR
    
    # Remove o último espaçamento de linha de cor, pois não há próxima linha
    if organized_dxfs and estimated_layout_height > 0:
        estimated_layout_height -= ESPACAMENTO_LINHA_COR
    
    # Se não houver itens nem plano, definimos uma altura mínima para evitar 0
    if estimated_layout_height == 0:
        estimated_layout_height = 1 # Altura mínima para um layout vazio

    # Agora, posicionamos os elementos de cima para baixo.
    current_y_pos_for_new_row = estimated_layout_height - MARGEM_INFERIOR # Começa do topo do espaço disponível

    # Inserir o DXF do plano de corte no topo, se houver
    if plano_entities:
        plano_insert_y = current_y_pos_for_new_row - plano_height
        
        offset_x_plano = MARGEM_ESQUERDA - plano_original_min_x
        offset_y_plano = plano_insert_y - plano_original_min_y

        for ent in plano_entities:
            new_ent = ent.copy()
            new_ent.translate(offset_x_plano, offset_y_plano, 0)
            all_relative_entities_with_coords.append((new_ent, new_ent.dxf.insert.x if hasattr(new_ent.dxf, 'insert') else offset_x_plano, new_ent.dxf.insert.y if hasattr(new_ent.dxf, 'insert') else offset_y_plano))
        
        print(f"[DEBUG] Plano de corte '{plan_name}.dxf' inserido em X:{MARGEM_ESQUERDA:.2f}, Y:{plano_insert_y:.2f} (relativo).")
        
        current_y_pos_for_new_row = plano_insert_y - ESPACAMENTO_PLANO_COR
        print(f"[DEBUG] Cursor Y após plano de corte: {current_y_pos_for_new_row:.2f} mm (abaixo do plano + espaçamento)")
    else:
        print("[DEBUG] Nenhum DXF de plano de corte para inserir.")


    # Ordenar cores para um layout consistente (ex: alfabético)
    sorted_colors = sorted(organized_dxfs.keys())

    for color_code in sorted_colors:
        color_group = organized_dxfs[color_code]
        current_x_pos = MARGEM_ESQUERDA # Reseta X para cada nova linha de cor
        
        # Encontra a altura máxima dos DXFs nesta linha de cor para determinar o avanço vertical
        max_height_in_color_line = 0
        if barra_entities:
            max_height_in_color_line = max(max_height_in_color_line, barra_height)

        for dxf_format in sorted(color_group.keys()):
            format_group = color_group[dxf_format]
            for dxf_size in sorted(format_group.keys()):
                size_group = format_group[dxf_size]
                for hole_type in size_group.keys():
                    for dxf_item in size_group[hole_type]:
                        max_height_in_color_line = max(max_height_in_color_line, dxf_item['bbox_height'])

        # A posição Y para esta linha de cor (canto inferior esquerdo dos itens)
        row_base_y = current_y_pos_for_new_row - max_height_in_color_line
        print(f"[DEBUG] Iniciando linha de cor '{color_code}'. Altura máx na linha: {max_height_in_color_line:.2f} mm. Base Y da linha: {row_base_y:.2f} mm")
        
        sorted_formats = sorted(color_group.keys())
        first_format_in_line = True
        for dxf_format in sorted_formats:
            format_group = color_group[dxf_format]

            if not first_format_in_line:
                # Inserir separador antes de um novo formato
                if barra_entities:
                    print(f"DEBUG: Inserindo Barra.dxf antes do novo formato '{dxf_format}'. current_x_pos antes: {current_x_pos:.2f} mm")
                    current_x_pos += ESPACAMENTO_SEPARADOR_PRE_BARRA
                    offset_x_barra = current_x_pos - barra_original_min_x
                    offset_y_barra = row_base_y - barra_original_min_y 
                    
                    for ent in barra_entities:
                        new_ent = ent.copy()
                        new_ent.translate(offset_x_barra, offset_y_barra, 0)
                        all_relative_entities_with_coords.append((new_ent, new_ent.dxf.insert.x if hasattr(new_ent.dxf, 'insert') else offset_x_barra, new_ent.dxf.insert.y if hasattr(new_ent.dxf, 'insert') else offset_y_barra))
                    print(f"[DEBUG] Barra.dxf inserida em X:{current_x_pos:.2f}, Y:{offset_y_barra:.2f}. Largura da barra: {barra_width:.2f} mm.")
                    current_x_pos += barra_width + ESPACAMENTO_SEPARADOR_POST_BARRA + ADJUSTMENT_OFFSET_BARRA_MM # Avança X pela largura da barra + espaçamento + ajuste
                else:
                    current_x_pos += ESPACAMENTO_DXF_MESMO_FURO # Fallback se a barra não for carregada
                print(f"[DEBUG] current_x_pos após barra (ou fallback) e espaçamento: {current_x_pos:.2f} mm")
            
            sorted_sizes = sorted(format_group.keys())
            first_size_in_format = True
            for dxf_size in sorted_sizes:
                size_group = format_group[dxf_size]

                if not first_size_in_format:
                    # Inserir separador antes de um novo tamanho
                    if barra_entities:
                        print(f"DEBUG: Inserindo Barra.dxf antes do novo tamanho '{dxf_size}'. current_x_pos antes: {current_x_pos:.2f} mm")
                        current_x_pos += ESPACAMENTO_SEPARADOR_PRE_BARRA
                        offset_x_barra = current_x_pos - barra_original_min_x
                        offset_y_barra = row_base_y - barra_original_min_y
                        for ent in barra_entities:
                            new_ent = ent.copy()
                            new_ent.translate(offset_x_barra, offset_y_barra, 0)
                            all_relative_entities_with_coords.append((new_ent, new_ent.dxf.insert.x if hasattr(new_ent.dxf, 'insert') else offset_x_barra, new_ent.dxf.insert.y if hasattr(new_ent.dxf, 'insert') else offset_y_barra))
                        print(f"[DEBUG] Barra.dxf inserida em X:{current_x_pos:.2f}, Y:{offset_y_barra:.2f}. Largura da barra: {barra_width:.2f} mm.")
                        current_x_pos += barra_width + ESPACAMENTO_SEPARADOR_POST_BARRA + ADJUSTMENT_OFFSET_BARRA_MM
                    else:
                        current_x_pos += ESPACAMENTO_DXF_MESMO_FURO
                    print(f"DEBUG: current_x_pos após barra (ou fallback) e espaçamento: {current_x_pos:.2f} mm")

                sorted_hole_types = sorted(size_group.keys())
                first_hole_type_in_size = True
                for hole_type in sorted_hole_types:
                    hole_type_group = size_group[hole_type]
                    
                    if not first_hole_type_in_size:
                        # Inserir separador antes de um novo tipo de furo
                        if barra_entities:
                            print(f"DEBUG: Inserindo Barra.dxf antes do novo furo '{hole_type}'. current_x_pos antes: {current_x_pos:.2f} mm")
                            current_x_pos += ESPACAMENTO_SEPARADOR_PRE_BARRA
                            offset_x_barra = current_x_pos - barra_original_min_x
                            offset_y_barra = row_base_y - barra_original_min_y
                            for ent in barra_entities:
                                new_ent = ent.copy()
                                new_ent.translate(offset_x_barra, offset_y_barra, 0)
                                all_relative_entities_with_coords.append((new_ent, new_ent.dxf.insert.x if hasattr(new_ent.dxf, 'insert') else offset_x_barra, new_ent.dxf.insert.y if hasattr(new_ent.dxf, 'insert') else offset_y_barra))
                            print(f"[DEBUG] Barra.dxf inserida em X:{current_x_pos:.2f}, Y:{offset_y_barra:.2f}. Largura da barra: {barra_width:.2f} mm.")
                            current_x_pos += barra_width + ESPACAMENTO_SEPARADOR_POST_BARRA + ADJUSTMENT_OFFSET_BARRA_MM
                        else:
                            current_x_pos += ESPACAMENTO_DXF_MESMO_FURO
                        print(f"DEBUG: current_x_pos após barra (ou fallback) e espaçamento: {current_x_pos:.2f} mm")
                    
                    # Ordenar DXFs dentro do grupo de furo (opcional, mas bom para consistência)
                    sorted_hole_type_dxfs = sorted(hole_type_group, key=lambda x: x['sku'])

                    first_dxf_in_group = True
                    for dxf_item in sorted_hole_type_dxfs:
                        entities = dxf_item['entities']
                        sku = dxf_item['sku']
                        bbox_width = dxf_item['bbox_width']
                        bbox_height = dxf_item['bbox_height']
                        original_min_x = dxf_item['original_min_x']
                        original_min_y = dxf_item['original_min_y']

                        if not first_dxf_in_group:
                            print(f"DEBUG: Adding ESPACAMENTO_DXF_MESMO_FURO ({ESPACAMENTO_DXF_MESMO_FURO:.2f} mm). current_x_pos before: {current_x_pos:.2f} mm")
                            current_x_pos += ESPACAMENTO_DXF_MESMO_FURO # Espaçamento entre DXFs do mesmo furo
                            print(f"DEBUG: current_x_pos after ESPACAMENTO_DXF_MESMO_FURO: {current_x_pos:.2f} mm")

                        # Calcular offset para mover o DXF para a posição atual (current_x_pos, row_base_y)
                        offset_x = current_x_pos - original_min_x
                        offset_y = row_base_y - original_min_y # Usar row_base_y para alinhar a base da linha

                        for ent in entities:
                            new_ent = ent.copy()
                            new_ent.translate(offset_x, offset_y, 0)
                            all_relative_entities_with_coords.append((new_ent, new_ent.dxf.insert.x if hasattr(new_ent.dxf, 'insert') else offset_x, new_ent.dxf.insert.y if hasattr(new_ent.dxf, 'insert') else offset_y))
                        
                        print(f"[DEBUG] Item '{sku}' inserido em X:{current_x_pos:.2f}, Y:{row_base_y:.2f} (relativo). Largura do item: {bbox_width:.2f} mm.")
                        current_x_pos += bbox_width # Avança X pela largura do DXF
                        print(f"DEBUG: current_x_pos after item '{sku}' (width {bbox_width:.2f}): {current_x_pos:.2f} mm")
                        first_dxf_in_group = False
                    
                    first_hole_type_in_size = False
                first_size_in_format = False
            first_format_in_line = False
        
        # Após processar todos os furos para uma cor, avança Y para a próxima linha de cor
        current_y_pos_for_new_row = row_base_y - ESPACAMENTO_LINHA_COR
        print(f"[DEBUG] Cursor Y após linha de cor '{color_code}': {current_y_pos_for_new_row:.2f} mm (abaixo da linha + espaçamento)")

    # --- 4. Calcular Bounding Box Final do Layout do Plano e Ajustar para (0,0) ---
    min_x_layout, min_y_layout, max_x_layout, max_y_layout = 0, 0, 0, 0
    
    if all_relative_entities_with_coords:
        from ezdxf.math import BoundingBox
        layout_bbox = BoundingBox()

        # Adiciona todas as entidades ao temp_msp para calcular o bbox
        for ent, _, _ in all_relative_entities_with_coords:
            temp_msp.add_entity(ent) # Adiciona a entidade já com a posição relativa calculada

        min_x_layout, min_y_layout, max_x_layout, max_y_layout = calcular_bbox_dxf(temp_msp)

        if min_x_layout == max_x_layout and min_y_layout == max_y_layout and len(all_relative_entities_with_coords) > 0:
            print("[WARN] Bounding box final do layout do plano ainda é 0x0. Pode haver entidades sem geometria. Retornando dimensões estimadas.")
            # Se o bbox ainda for 0x0, mas há entidades, usamos as dimensões estimadas para o layout
            # e não levantamos NoEntitiesFoundError aqui, pois queremos que o DXF seja gerado (mesmo que com problemas visuais).
            layout_width = MARGEM_ESQUERDA * 2 + 100 # Exemplo de largura mínima
            layout_height = estimated_layout_height # Usa a altura estimada
            return [(ent, x, y) for ent, x, y in all_relative_entities_with_coords], layout_width, layout_height, failed_ids_current_plan
            
    else:
        print("[INFO] Nenhum item ou plano para o layout. Retornando layout vazio e lista de falhas.")
        # Se não há entidades, o layout é considerado vazio, o que é um cenário de falha para a geração do DXF.
        # Levantamos a exceção aqui, pois não há nada para desenhar.
        raise NoEntitiesFoundError(f"Nenhum item ou plano válido para o layout do plano '{plan_name}'.")

    # Ajustar todas as entidades para que o canto inferior esquerdo do layout seja (0,0)
    offset_x_final = -min_x_layout
    offset_y_final = -min_y_layout

    final_entities_with_coords = []
    for ent, current_x, current_y in all_relative_entities_with_coords:
        new_ent = ent.copy() # Copia novamente para não modificar a referência original
        new_ent.translate(offset_x_final, offset_y_final, 0)
        final_entities_with_coords.append((new_ent, current_x + offset_x_final, current_y + offset_y_final))

    layout_width = max_x_layout - min_x_layout
    layout_height = max_y_layout - min_y_layout

    print(f"[INFO] Layout do plano '{plan_name}' gerado. Dimensões: {layout_width:.2f}x{layout_height:.2f} mm")
    return final_entities_with_coords, layout_width, layout_height, failed_ids_current_plan

print("DEBUG: dxf_layout_engine.py - generate_single_plan_layout_data() definida.")
print("DEBUG: dxf_layout_engine.py - Fim do carregamento do módulo.")
