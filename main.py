# Cole seu código aqui
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional, Tuple, Any
import os
import datetime
import ezdxf # Importa ezdxf aqui

# Importações das funções de composição DXF e de interação com o Google Drive
from dxf_layout_engine import generate_single_plan_layout_data, FOLHA_LARGURA_MM, ESPACAMENTO_LINHA_COR, NoEntitiesFoundError # Importa a nova função, constantes e a exceção
from google_drive_utils import upload_to_drive, mover_arquivos_antigos, buscar_arquivo_personalizado_por_id_e_sku, esvaziar_lixeira_drive, deletar_todos_os_arquivos

app = FastAPI()

# --- Configuração CORS ---
origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- Fim da Configuração CORS ---

class ItemEntrada(BaseModel):
    """
    Define o modelo de dados para cada item DXF a ser composto.
    - id_arquivo_drive: O ID do arquivo DXF no Google Drive.
    - sku: O SKU completo do item (ex: PLAC-3010-2FH-AC-DOU-070-00000).
    """
    id_arquivo_drive: str = Field(..., description="ID do arquivo DXF no Google Drive.")
    sku: str = Field(..., description="SKU completo do item (ex: PLAC-3010-2FH-AC-DOU-070-00000).")

class PlanData(BaseModel):
    """
    Define o modelo de dados para cada plano de corte dentro da requisição.
    """
    plan_name: str = Field(..., description="Nome do plano de corte (ex: '01', 'A').")
    items: List[ItemEntrada] = Field(..., min_items=1, description="Lista de itens DXF para este plano.")

class EntradaComposicao(BaseModel):
    """
    Define o modelo de dados para a entrada da requisição POST para composição.
    - plans: Lista de objetos PlanData, cada um representando um plano de corte.
    - id_pasta_entrada_drive: O ID da pasta do Google Drive de onde os arquivos DXF personalizados são lidos.
    - id_pasta_saida_drive: O ID da pasta do Google Drive onde o DXF gerado será salvo.
    - output_filename: Opcional. Nome do arquivo DXF de saída. Se não fornecido, será gerado automaticamente.
    """
    plans: List[PlanData] = Field(..., min_items=1, description="Lista de planos de corte a serem compostos.")
    id_pasta_entrada_drive: str = Field(..., description="ID da pasta do Google Drive de onde os arquivos DXF personalizados são lidos.")
    id_pasta_saida_drive: str = Field(..., description="ID da pasta do Google Drive onde o DXF gerado será salvo.")
    output_filename: Optional[str] = Field(None, description="Nome do arquivo DXF de saída. Se não fornecido, será gerado automaticamente.")


@app.post("/compor-plano")
async def compor_plano(entrada: EntradaComposicao):
    """
    Endpoint para compor um novo arquivo DXF combinando múltiplos planos de corte,
    organizando-os verticalmente. O arquivo DXF resultante é enviado para a pasta
    de saída especificada no Google Drive.
    """
    if not entrada.plans:
        raise HTTPException(status_code=400, detail="Nenhum plano fornecido para composição.")

    print(f"[INFO] Iniciando composição de múltiplos planos.")
    print(f"[INFO] ID da pasta de entrada do Drive: {entrada.id_pasta_entrada_drive}")
    print(f"[INFO] ID da pasta de saída do Drive: {entrada.id_pasta_saida_drive}")
    print(f"[INFO] Total de planos a processar: {len(entrada.plans)}")
    if entrada.output_filename:
        print(f"[INFO] Nome de arquivo de saída especificado: {entrada.output_filename}")

    # Cria um novo documento DXF principal
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()

    # Variáveis para controlar o posicionamento vertical global
    current_y_offset_global = 0.0 # Começa do fundo do documento e cresce para cima
    max_overall_width = 0.0
    all_failed_ids = [] # Lista para coletar todos os IDs de arquivo que falharam em qualquer plano

    # Ordena os planos pelo nome para garantir uma ordem consistente (ex: 01, 02, A, B)
    sorted_plans = sorted(entrada.plans, key=lambda p: p.plan_name)

    try:
        for i, plan_data in enumerate(sorted_plans):
            print(f"[INFO] Processando plano '{plan_data.plan_name}' ({i+1}/{len(sorted_plans)})...")
            
            try:
                # Gera os dados do layout para um único plano
                # Agora retorna também a lista de IDs de arquivos que falharam neste plano
                entities_with_relative_coords, layout_width, layout_height, failed_ids_current_plan = \
                    generate_single_plan_layout_data(
                        file_ids_and_skus=[item.model_dump() for item in plan_data.items],
                        plan_name=plan_data.plan_name,
                        drive_folder_id=entrada.id_pasta_entrada_drive
                    )
                
                # Adiciona os IDs falhos deste plano à lista geral de falhas
                all_failed_ids.extend(failed_ids_current_plan)

                # Se a função retornou, mas não há entidades (após pular falhas),
                # isso significa que o plano não pôde ser gerado.
                if not entities_with_relative_coords:
                    print(f"[WARN] Plano '{plan_data.plan_name}' não gerou entidades visíveis após processar itens. Pulando este plano.")
                    # Não levantamos um erro fatal aqui, pois queremos continuar com outros planos.
                    continue 

                # Atualiza a largura máxima geral do documento
                max_overall_width = max(max_overall_width, layout_width)

                # Adiciona as entidades do plano atual ao modelspace principal, com o offset vertical
                for ent, x_rel, y_rel in entities_with_relative_coords:
                    # O (x_rel, y_rel) já é relativo ao canto inferior esquerdo do layout do plano.
                    # Precisamos transladar para a posição global no documento principal.
                    # current_y_offset_global é a base Y para o plano atual.
                    new_ent = ent.copy() # Copia a entidade novamente para o novo documento
                    new_ent.translate(0, current_y_offset_global, 0) # Aplica o offset vertical
                    msp.add_entity(new_ent)
                    
                print(f"[DEBUG] Plano '{plan_data.plan_name}' adicionado ao DXF principal na altura Y: {current_y_offset_global:.2f} mm.")

                # Atualiza o offset Y global para o próximo plano
                # Adiciona a altura do layout do plano atual mais um espaçamento entre planos
                current_y_offset_global += layout_height + ESPACAMENTO_LINHA_COR # Reutiliza ESPACAMENTO_LINHA_COR para planos

            except NoEntitiesFoundError as e:
                # Esta exceção agora só é levantada se o plano inteiro não tiver entidades válidas
                print(f"[ERROR] Erro na geração do layout para o plano '{plan_data.plan_name}': {e}. Este plano será ignorado no DXF final.")
                # Adiciona todos os IDs de arquivo do plano atual à lista de falhas, pois o plano não foi gerado
                all_failed_ids.extend([item.id_arquivo_drive for item in plan_data.items])
                continue # Continua para o próximo plano, pois queremos um DXF parcial se possível
            except FileNotFoundError as e:
                print(f"[ERROR] Erro de arquivo não encontrado para o plano '{plan_data.plan_name}': {e}. Este plano será ignorado no DXF final.")
                all_failed_ids.extend([item.id_arquivo_drive for item in plan_data.items])
                continue
            except Exception as e:
                print(f"[ERROR] Erro inesperado ao processar plano '{plan_data.plan_name}': {e}. Este plano será ignorado no DXF final.")
                all_failed_ids.extend([item.id_arquivo_drive for item in plan_data.items])
                continue

        # Verifica se alguma entidade foi realmente adicionada ao documento principal
        if not msp:
            # Se, após tentar processar todos os planos, o modelspace ainda estiver vazio,
            # significa que nenhum DXF válido pôde ser gerado.
            raise HTTPException(status_code=400, detail="Nenhuma entidade DXF válida foi gerada para nenhum dos planos fornecidos. Verifique os SKUs e IDs dos arquivos.")

        # Nome do arquivo de saída
        if entrada.output_filename:
            output_dxf_name = entrada.output_filename
        else:
            timestamp = datetime.datetime.now().strftime("%d-%m-%Y_%H%M%S")
            # Gera um nome de arquivo com todos os planos envolvidos
            plan_names_in_filename = " - ".join(p.plan_name for p in sorted_plans)
            output_dxf_name = f"Plano de Gravação {plan_names_in_filename} {timestamp}.dxf"

        # Caminho temporário para salvar localmente antes do upload
        caminho_saida_dxf = f"/tmp/{output_dxf_name}"

        os.makedirs(os.path.dirname(caminho_saida_dxf) or '.', exist_ok=True)
        doc.saveas(caminho_saida_dxf)
        print(f"[INFO] DXF de saída salvo: {caminho_saida_dxf}")
        
        # Faz o upload do arquivo DXF gerado para o Google Drive
        url_dxf = upload_to_drive(
            caminho_saida_dxf,
            os.path.basename(caminho_saida_dxf),
            "application/dxf",
            entrada.id_pasta_saida_drive
        )

        # Limpa o arquivo temporário após o upload
        if os.path.exists(caminho_saida_dxf):
            os.remove(caminho_saida_dxf)
            print(f"[INFO] Arquivo temporário DXF removido: {caminho_saida_dxf}")

        print(f"[INFO] Composição de múltiplos planos concluída com sucesso.")
        return {
            "message": "Plano de corte DXF composto e enviado ao Google Drive com sucesso!",
            "dxf_url": url_dxf,
            "failed_items": all_failed_ids # Retorna a lista de IDs de arquivo que falharam
        }

    except HTTPException as e:
        # Re-levanta HTTPException para que o FastAPI a capture e retorne ao cliente
        raise e
    except FileNotFoundError as e:
        print(f"[ERROR] Erro de arquivo não encontrado: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        print(f"[ERROR] Erro na composição do plano: {e}")
        raise HTTPException(status_code=500, detail=f"Erro interno ao processar a requisição: {e}")

@app.post("/mover-arquivos-antigos")
async def mover_antigos_endpoint(id_pasta_drive: str):
    """
    Endpoint para mover arquivos DXF e PNG antigos (com data diferente da atual)
    para uma subpasta 'arquivo morto' no Google Drive.
    """
    print(f"[INFO] Iniciando movimentação de arquivos antigos na pasta: {id_pasta_drive}")
    try:
        moved_count = mover_arquivos_antigos(drive_folder_id=id_pasta_drive)
        print(f"[INFO] {moved_count} arquivos antigos movidos com sucesso.")
        return {"message": f"{moved_count} arquivos antigos movidos para 'arquivo morto'."}
    except Exception as e:
        print(f"[ERROR] Erro ao mover arquivos antigos: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao mover arquivos antigos: {e}")

@app.post("/esvaziar-lixeira")
async def esvaziar_lixeira_endpoint():
    """
    Endpoint para esvaziar permanentemente a lixeira da conta de serviço no Google Drive.
    """
    print("[INFO] Requisição para esvaziar a lixeira do Drive recebida.")
    try:
        esvaziar_lixeira_drive()
        return {"message": "Lixeira do Google Drive esvaziada com sucesso."}
    except Exception as e:
        print(f"[ERROR] Falha ao tentar esvaziar a lixeira: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao esvaziar a lixeira: {e}")

@app.post("/deletar-tudo")
async def deletar_tudo_endpoint():
    """
    !!! CUIDADO: ENDPOINT DESTRUTIVO !!!
    Endpoint para excluir permanentemente TODOS os arquivos (não pastas) que pertencem
    à conta de serviço no Google Drive. Esta ação é IRREVERSÍVEL.
    Use para liberar espaço quando a conta estiver cheia e a limpeza da lixeira não for suficiente.
    """
    print("[WARN] Requisição para DELETAR TODOS OS ARQUIVOS da conta de serviço recebida.")
    try:
        deleted_count = deletar_todos_os_arquivos()
        return {"message": f"Operação concluída. {deleted_count} arquivos foram excluídos permanentemente da conta de serviço."}
    except Exception as e:
        print(f"[FATAL] Falha catastrófica ao tentar deletar todos os arquivos: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao deletar todos os arquivos: {e}")

@app.get("/")
async def root():
    return {"message": "API de Composição DXF e Gerenciamento de Drive está online!"}

