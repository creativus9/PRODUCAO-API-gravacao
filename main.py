from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
import os
import datetime
import ezdxf

# Importações das funções de composição DXF, SVG e de interação com o Google Drive
from dxf_layout_engine import generate_single_plan_layout_data, ESPACAMENTO_LINHA_COR, NoEntitiesFoundError
from svg_layout_engine import generate_single_plan_svg_layout_data, create_svg_document, save_svg_document
from google_drive_utils import (
    upload_to_drive,
    mover_arquivos_antigos,
    buscar_arquivo_personalizado_por_id_e_sku,
    esvaziar_lixeira_drive,
    deletar_todos_os_arquivos,
)

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
    Define o modelo de dados para cada item a ser composto.
    - id_arquivo_drive: O ID lógico do arquivo personalizado no Google Drive.
    - sku: O SKU completo do item (ex: PLAC-3010-2FH-AC-DOU-070-00000).
    """
    id_arquivo_drive: str = Field(..., description="ID lógico do arquivo personalizado no Google Drive.")
    sku: str = Field(..., description="SKU completo do item (ex: PLAC-3010-2FH-AC-DOU-070-00000).")


class PlanData(BaseModel):
    """
    Define o modelo de dados para cada plano de corte/gravação dentro da requisição.
    """
    plan_name: str = Field(..., description="Nome do plano (ex: '01', 'A').")
    items: List[ItemEntrada] = Field(..., min_items=1, description="Lista de itens para este plano.")


class EntradaComposicao(BaseModel):
    """
    Define o modelo de dados da requisição POST para composição.
    - plans: Lista de objetos PlanData, cada um representando um plano.
    - id_pasta_entrada_drive: ID da pasta do Drive de onde os arquivos personalizados são lidos.
    - id_pasta_saida_drive: ID da pasta do Drive onde o arquivo gerado será salvo.
    - output_filename: Opcional. Nome do arquivo de saída. A extensão será ajustada automaticamente (.dxf ou .svg).
    """
    plans: List[PlanData] = Field(..., min_items=1, description="Lista de planos a serem compostos.")
    id_pasta_entrada_drive: str = Field(..., description="ID da pasta do Google Drive de onde os arquivos personalizados são lidos.")
    id_pasta_saida_drive: str = Field(..., description="ID da pasta do Google Drive onde o arquivo gerado será salvo.")
    output_filename: Optional[str] = Field(None, description="Nome do arquivo de saída. Se não fornecido, será gerado automaticamente.")


def _model_to_dict(model):
    """Compatibilidade com Pydantic v1/v2."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _normalizar_nome_saida(nome_informado: Optional[str], extensao: str, sorted_plans: list) -> str:
    if not extensao.startswith("."):
        extensao = f".{extensao}"

    if nome_informado:
        base, ext = os.path.splitext(nome_informado)
        if ext.lower() != extensao.lower():
            return f"{base}{extensao}"
        return nome_informado

    timestamp = datetime.datetime.now().strftime("%d-%m-%Y_%H%M%S")
    plan_names_in_filename = " - ".join(p["plan_name"] for p in sorted_plans)
    return f"Plano de Gravação {plan_names_in_filename} {timestamp}{extensao}"


def _resolver_tipo_e_arquivos(entrada: EntradaComposicao):
    """
    Resolve previamente se a composição será DXF ou SVG.
    Regra: todos os arquivos encontrados precisam ter a mesma extensão.
    Misturar .dxf e .svg retorna erro 400 antes de montar qualquer arquivo.
    """
    sorted_plans = sorted(entrada.plans, key=lambda p: p.plan_name)
    resolved_plans = []
    tipos_encontrados = set()
    exemplos_por_tipo = {".dxf": [], ".svg": []}
    ids_nao_resolvidos = []

    for plan_data in sorted_plans:
        resolved_items = []
        for item in plan_data.items:
            item_dict = _model_to_dict(item)
            target_id = item.id_arquivo_drive
            sku = item.sku

            try:
                real_file_id, nome_arquivo_drive, extensao = buscar_arquivo_personalizado_por_id_e_sku(
                    target_id=target_id,
                    sku=sku,
                    drive_folder_id=entrada.id_pasta_entrada_drive,
                    extensoes_permitidas=(".dxf", ".svg"),
                    retornar_extensao=True,
                )

                item_dict.update({
                    "arquivo_drive_id_resolvido": real_file_id,
                    "nome_arquivo_drive": nome_arquivo_drive,
                    "extensao_arquivo": extensao,
                })
                tipos_encontrados.add(extensao)

                if len(exemplos_por_tipo.get(extensao, [])) < 5:
                    exemplos_por_tipo.setdefault(extensao, []).append(f"{target_id} -> {nome_arquivo_drive}")

            except FileNotFoundError as exc:
                # Não quebra aqui para preservar o comportamento antigo: itens não encontrados
                # ainda entram como falha no motor correspondente.
                print(f"[WARN] Não foi possível resolver previamente o ID '{target_id}' / SKU '{sku}': {exc}")
                ids_nao_resolvidos.append(target_id)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc))

            resolved_items.append(item_dict)

        resolved_plans.append({"plan_name": plan_data.plan_name, "items": resolved_items})

    if not tipos_encontrados:
        raise HTTPException(
            status_code=400,
            detail=(
                "Não foi possível identificar se a entrada é DXF ou SVG, pois nenhum arquivo personalizado "
                "foi encontrado/resolvido no Drive. Verifique os IDs, SKUs e nomes dos arquivos."
            ),
        )

    if len(tipos_encontrados) > 1:
        detalhes = []
        if exemplos_por_tipo[".dxf"]:
            detalhes.append("DXF: " + "; ".join(exemplos_por_tipo[".dxf"]))
        if exemplos_por_tipo[".svg"]:
            detalhes.append("SVG: " + "; ".join(exemplos_por_tipo[".svg"]))

        raise HTTPException(
            status_code=400,
            detail=(
                "Você misturou arquivos DXF e SVG na mesma composição. "
                "Para evitar erro no plano, envie tudo em DXF ou tudo em SVG. "
                + " | ".join(detalhes)
            ),
        )

    tipo_entrada = next(iter(tipos_encontrados))
    return tipo_entrada, resolved_plans, ids_nao_resolvidos


async def _compor_plano_dxf(entrada: EntradaComposicao, sorted_plans: list, ids_nao_resolvidos: list):
    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 4
    msp = doc.modelspace()

    current_y_offset_global = 0.0
    max_overall_width = 0.0
    all_failed_ids = list(ids_nao_resolvidos)

    for i, plan_data in enumerate(sorted_plans):
        print(f"[INFO] Processando plano DXF '{plan_data['plan_name']}' ({i+1}/{len(sorted_plans)})...")

        try:
            entities_with_relative_coords, layout_width, layout_height, failed_ids_current_plan = generate_single_plan_layout_data(
                file_ids_and_skus=plan_data["items"],
                plan_name=plan_data["plan_name"],
                drive_folder_id=entrada.id_pasta_entrada_drive,
            )

            all_failed_ids.extend(failed_ids_current_plan)

            if not entities_with_relative_coords:
                print(f"[WARN] Plano '{plan_data['plan_name']}' não gerou entidades visíveis. Pulando este plano.")
                continue

            max_overall_width = max(max_overall_width, layout_width)

            for ent, x_rel, y_rel in entities_with_relative_coords:
                new_ent = ent.copy()
                new_ent.translate(0, current_y_offset_global, 0)
                msp.add_entity(new_ent)

            print(f"[DEBUG] Plano DXF '{plan_data['plan_name']}' adicionado na altura Y: {current_y_offset_global:.2f} mm.")
            current_y_offset_global += layout_height + ESPACAMENTO_LINHA_COR

        except NoEntitiesFoundError as exc:
            print(f"[ERROR] Erro na geração do layout DXF para o plano '{plan_data['plan_name']}': {exc}")
            all_failed_ids.extend([item["id_arquivo_drive"] for item in plan_data["items"]])
            continue
        except FileNotFoundError as exc:
            print(f"[ERROR] Arquivo não encontrado no plano DXF '{plan_data['plan_name']}': {exc}")
            all_failed_ids.extend([item["id_arquivo_drive"] for item in plan_data["items"]])
            continue
        except Exception as exc:
            print(f"[ERROR] Erro inesperado ao processar plano DXF '{plan_data['plan_name']}': {exc}")
            all_failed_ids.extend([item["id_arquivo_drive"] for item in plan_data["items"]])
            continue

    if not msp:
        raise HTTPException(
            status_code=400,
            detail="Nenhuma entidade DXF válida foi gerada para nenhum dos planos fornecidos. Verifique os SKUs e IDs dos arquivos.",
        )

    output_dxf_name = _normalizar_nome_saida(entrada.output_filename, ".dxf", sorted_plans)
    caminho_saida_dxf = f"/tmp/{output_dxf_name}"

    os.makedirs(os.path.dirname(caminho_saida_dxf) or '.', exist_ok=True)
    doc.saveas(caminho_saida_dxf)
    print(f"[INFO] DXF de saída salvo: {caminho_saida_dxf}")

    url_dxf = upload_to_drive(
        caminho_saida_dxf,
        os.path.basename(caminho_saida_dxf),
        "application/dxf",
        entrada.id_pasta_saida_drive,
    )

    if os.path.exists(caminho_saida_dxf):
        os.remove(caminho_saida_dxf)
        print(f"[INFO] Arquivo temporário DXF removido: {caminho_saida_dxf}")

    return {
        "message": "Plano DXF composto e enviado ao Google Drive com sucesso!",
        "output_type": "dxf",
        "dxf_url": url_dxf,
        "failed_items": all_failed_ids,
    }


async def _compor_plano_svg(entrada: EntradaComposicao, sorted_plans: list, ids_nao_resolvidos: list):
    current_y_offset_global = 0.0
    max_overall_width = 1.0
    all_failed_ids = list(ids_nao_resolvidos)
    plan_groups = []

    for i, plan_data in enumerate(sorted_plans):
        print(f"[INFO] Processando plano SVG '{plan_data['plan_name']}' ({i+1}/{len(sorted_plans)})...")

        try:
            plan_group, layout_width, layout_height, failed_ids_current_plan = generate_single_plan_svg_layout_data(
                file_ids_and_skus=plan_data["items"],
                plan_name=plan_data["plan_name"],
                drive_folder_id=entrada.id_pasta_entrada_drive,
                global_y_offset=current_y_offset_global,
            )

            all_failed_ids.extend(failed_ids_current_plan)

            if len(list(plan_group)) == 0:
                print(f"[WARN] Plano SVG '{plan_data['plan_name']}' não gerou elementos visíveis. Pulando este plano.")
                continue

            # O deslocamento vertical já foi aplicado diretamente em cada item
            # pelo motor SVG. Não usamos transform no grupo inteiro do plano,
            # pois o LightBurn pode ignorá-lo em montagens complexas.
            plan_groups.append(plan_group)
            max_overall_width = max(max_overall_width, layout_width)
            current_y_offset_global += layout_height + ESPACAMENTO_LINHA_COR

        except NoEntitiesFoundError as exc:
            print(f"[ERROR] Erro na geração do layout SVG para o plano '{plan_data['plan_name']}': {exc}")
            all_failed_ids.extend([item["id_arquivo_drive"] for item in plan_data["items"]])
            continue
        except FileNotFoundError as exc:
            print(f"[ERROR] Arquivo não encontrado no plano SVG '{plan_data['plan_name']}': {exc}")
            all_failed_ids.extend([item["id_arquivo_drive"] for item in plan_data["items"]])
            continue
        except Exception as exc:
            print(f"[ERROR] Erro inesperado ao processar plano SVG '{plan_data['plan_name']}': {exc}")
            all_failed_ids.extend([item["id_arquivo_drive"] for item in plan_data["items"]])
            continue

    if not plan_groups:
        raise HTTPException(
            status_code=400,
            detail="Nenhum SVG válido foi gerado para nenhum dos planos fornecidos. Verifique os SKUs, IDs e arquivos SVG.",
        )

    total_height = max(1.0, current_y_offset_global - ESPACAMENTO_LINHA_COR)
    svg_root = create_svg_document(max_overall_width, total_height)
    for group in plan_groups:
        svg_root.append(group)

    output_svg_name = _normalizar_nome_saida(entrada.output_filename, ".svg", sorted_plans)
    caminho_saida_svg = f"/tmp/{output_svg_name}"
    save_svg_document(svg_root, caminho_saida_svg)
    print(f"[INFO] SVG de saída salvo: {caminho_saida_svg}")

    url_svg = upload_to_drive(
        caminho_saida_svg,
        os.path.basename(caminho_saida_svg),
        "image/svg+xml",
        entrada.id_pasta_saida_drive,
    )

    if os.path.exists(caminho_saida_svg):
        os.remove(caminho_saida_svg)
        print(f"[INFO] Arquivo temporário SVG removido: {caminho_saida_svg}")

    return {
        "message": "Plano SVG composto e enviado ao Google Drive com sucesso!",
        "output_type": "svg",
        "svg_url": url_svg,
        "failed_items": all_failed_ids,
    }


@app.post("/compor-plano")
async def compor_plano(entrada: EntradaComposicao):
    """
    Endpoint híbrido para compor planos.
    - Se todos os arquivos de entrada forem DXF, gera um DXF como antes.
    - Se todos os arquivos de entrada forem SVG, gera um SVG de saída sem converter para DXF.
    - Se houver mistura de DXF e SVG, retorna erro explicando que deve ser tudo DXF ou tudo SVG.
    """
    if not entrada.plans:
        raise HTTPException(status_code=400, detail="Nenhum plano fornecido para composição.")

    print("[INFO] Iniciando composição híbrida de múltiplos planos.")
    print(f"[INFO] ID da pasta de entrada do Drive: {entrada.id_pasta_entrada_drive}")
    print(f"[INFO] ID da pasta de saída do Drive: {entrada.id_pasta_saida_drive}")
    print(f"[INFO] Total de planos a processar: {len(entrada.plans)}")
    if entrada.output_filename:
        print(f"[INFO] Nome de arquivo de saída especificado: {entrada.output_filename}")

    try:
        tipo_entrada, sorted_plans, ids_nao_resolvidos = _resolver_tipo_e_arquivos(entrada)
        print(f"[INFO] Tipo detectado para composição: {tipo_entrada}")

        if tipo_entrada == ".dxf":
            return await _compor_plano_dxf(entrada, sorted_plans, ids_nao_resolvidos)

        if tipo_entrada == ".svg":
            return await _compor_plano_svg(entrada, sorted_plans, ids_nao_resolvidos)

        raise HTTPException(status_code=400, detail=f"Tipo de arquivo não suportado: {tipo_entrada}")

    except HTTPException:
        raise
    except FileNotFoundError as exc:
        print(f"[ERROR] Erro de arquivo não encontrado: {exc}")
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        print(f"[ERROR] Erro na composição do plano: {exc}")
        raise HTTPException(status_code=500, detail=f"Erro interno ao processar a requisição: {exc}")


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
    return {"message": "API híbrida de Composição DXF/SVG e Gerenciamento de Drive está online!"}
