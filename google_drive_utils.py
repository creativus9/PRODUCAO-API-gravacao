import os
import json
import datetime
import re # Importar módulo de expressões regulares
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# Carrega credenciais direto da variável de ambiente
SERVICE_ACCOUNT_JSON = os.getenv("service_account.json")
if not SERVICE_ACCOUNT_JSON:
    raise Exception("Variável de ambiente 'service_account.json' não foi encontrada. Por favor, configure-a no Railway.")

try:
    info = json.loads(SERVICE_ACCOUNT_JSON)
except json.JSONDecodeError:
    raise Exception("Conteúdo da variável 'service_account.json' não é um JSON válido.")

creds = service_account.Credentials.from_service_account_info(
    info,
    scopes=["https://www.googleapis.com/auth/drive"]
)

drive_service = build('drive', 'v3', credentials=creds)

DEFAULT_FOLDER_ID = "1fLWrdK6MUhbeyBDvWHjz-2bTmZ2GB0ap"


def baixar_arquivo_drive(file_id: str, nome_arquivo_local: str, drive_folder_id: str = DEFAULT_FOLDER_ID):
    """
    Baixa um arquivo do Google Drive usando seu ID.
    Salva o arquivo em um caminho temporário local e retorna esse caminho.
    """
    local_path = f"/tmp/{nome_arquivo_local}"
    try:
        request = drive_service.files().get_media(fileId=file_id)
        with open(local_path, 'wb') as f:
            data = request.execute()
            f.write(data)
        print(f"[INFO] Arquivo '{nome_arquivo_local}' (ID: {file_id}) baixado para '{local_path}'.")
        return local_path
    except HttpError as error:
        if error.resp.status == 404:
            raise FileNotFoundError(f"Arquivo com ID '{file_id}' não encontrado no Drive.")
        else:
            raise Exception(f"Erro ao baixar arquivo com ID '{file_id}': {error}")
    except Exception as e:
        raise Exception(f"Erro inesperado ao baixar arquivo com ID '{file_id}': {e}")


def buscar_arquivo_personalizado_por_id_e_sku(
    target_id: str,
    sku: str,
    drive_folder_id: str = DEFAULT_FOLDER_ID,
    extensoes_permitidas=(".dxf",),
    retornar_extensao: bool = False,
):
    """
    Busca um arquivo personalizado no Google Drive que contenha o ID lógico
    e "Arquivo Personalizado" no nome, dentro da pasta especificada.

    Por padrão mantém o comportamento antigo: retorna somente arquivos .dxf
    e devolve (id, nome). Para o modo híbrido, passe extensoes_permitidas
    como (".dxf", ".svg") e retornar_extensao=True.
    """
    escaped_target_id = re.escape(target_id)

    if isinstance(extensoes_permitidas, str):
        extensoes_permitidas = (extensoes_permitidas,)

    extensoes_normalizadas = []
    for ext in extensoes_permitidas:
        ext = str(ext).lower().strip()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = f".{ext}"
        extensoes_normalizadas.append(ext)

    if not extensoes_normalizadas:
        extensoes_normalizadas = [".dxf"]

    query = (
        f"'{drive_folder_id}' in parents and "
        f"name contains '{target_id}' and "
        f"name contains 'Arquivo Personalizado' and "
        f"mimeType != 'application/vnd.google-apps.folder'"
    )

    try:
        response = drive_service.files().list(q=query, fields="files(id, name)").execute()
        files = response.get('files', [])

        exts_regex = "|".join(re.escape(ext.lstrip(".")) for ext in extensoes_normalizadas)
        pattern = re.compile(
            rf"^{escaped_target_id}\s*-\s*Arquivo Personalizado.*\.({exts_regex})$",
            re.IGNORECASE,
        )

        found_files = []
        for f in files:
            file_name = f.get('name', '')
            match = pattern.match(file_name)
            if match:
                extension = f".{match.group(1).lower()}"
                found_files.append({**f, "extension": extension})

        if not found_files:
            allowed = ", ".join(extensoes_normalizadas)
            raise FileNotFoundError(
                f"Nenhum arquivo 'Arquivo Personalizado' com ID '{target_id}' "
                f"e extensão permitida ({allowed}) encontrado no Drive."
            )

        found_extensions = sorted({f["extension"] for f in found_files})
        if retornar_extensao and len(found_extensions) > 1:
            raise Exception(
                f"Arquivo personalizado ambíguo para ID '{target_id}'. Foram encontrados "
                f"arquivos {', '.join(found_extensions)}. Deixe apenas uma extensão por item."
            )

        extension_priority = {ext: index for index, ext in enumerate(extensoes_normalizadas)}
        found_files.sort(key=lambda f: (extension_priority.get(f["extension"], 999), f.get("name", "")))
        selected_file = found_files[0]

        if retornar_extensao:
            return selected_file['id'], selected_file['name'], selected_file['extension']

        return selected_file['id'], selected_file['name']

    except HttpError as error:
        raise Exception(f"Erro ao buscar arquivo personalizado para ID '{target_id}': {error}")
    except FileNotFoundError:
        raise
    except Exception as e:
        raise Exception(f"Erro inesperado ao buscar arquivo personalizado para ID '{target_id}': {e}")

def upload_to_drive(caminho_arquivo_local: str, nome_arquivo_drive: str, mime_type: str, drive_folder_id: str = DEFAULT_FOLDER_ID):
    """
    Faz upload de um arquivo para o Google Drive e retorna sua URL pública.
    """
    file_metadata = {'name': nome_arquivo_drive, 'parents': [drive_folder_id]}
    media = MediaFileUpload(caminho_arquivo_local, mimetype=mime_type)
    
    try:
        file = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()
        drive_service.permissions().create(
            fileId=file.get('id'), body={'role':'reader','type':'anyone'}
        ).execute()
        public_url = f"https://drive.google.com/file/d/{file.get('id')}/view"
        print(f"[INFO] Arquivo '{nome_arquivo_drive}' enviado ao Drive: {public_url}")
        return public_url
    except HttpError as error:
        raise Exception(f"Erro ao fazer upload do arquivo '{nome_arquivo_drive}': {error}")
    except Exception as e:
        raise Exception(f"Erro inesperado ao fazer upload do arquivo '{nome_arquivo_drive}': {e}")


def mover_arquivos_antigos(drive_folder_id: str = DEFAULT_FOLDER_ID):
    """
    Move arquivos .dxf e .png com data diferente da atual para subpasta 'arquivo morto'.
    Retorna quantidade movida.
    """
    hoje = datetime.datetime.now().strftime("%d-%m-%Y")
    
    query_folder = f"'{drive_folder_id}' in parents and name='arquivo morto' and mimeType='application/vnd.google-apps.folder'"
    res_folder = drive_service.files().list(q=query_folder, fields="files(id)").execute().get('files', [])
    
    dest_id = None
    if res_folder:
        dest_id = res_folder[0]['id']
    else:
        try:
            meta = {'name':'arquivo morto','mimeType':'application/vnd.google-apps.folder','parents':[drive_folder_id]}
            folder = drive_service.files().create(body=meta, fields='id').execute()
            dest_id = folder.get('id')
        except HttpError as error:
            raise Exception(f"Erro ao criar subpasta 'arquivo morto': {error}")

    if not dest_id:
        raise Exception("Não foi possível encontrar ou criar a pasta 'arquivo morto'.")

    query_files = f"'{drive_folder_id}' in parents and (mimeType='application/dxf' or mimeType='image/png')"
    resp_files = drive_service.files().list(q=query_files, fields="files(id,name,parents)").execute()
    files = resp_files.get('files', [])
    
    moved_count = 0
    for f in files:
        name = f.get('name', '')
        match = re.search(r'(\d{2}-\d{2}-\d{4})\.(dxf|png)$', name)
        
        if match:
            file_date_str = match.group(1)
            if file_date_str != hoje:
                try:
                    drive_service.files().update(
                        fileId=f['id'],
                        addParents=dest_id,
                        removeParents=drive_folder_id,
                        fields='id, parents'
                    ).execute()
                    print(f"[INFO] Arquivo '{name}' movido para 'arquivo morto'.")
                    moved_count += 1
                except HttpError as error:
                    print(f"[ERROR] Falha ao mover '{name}': {error}")
    return moved_count

def arquivo_existe_drive(nome_arquivo: str, drive_folder_id: str = DEFAULT_FOLDER_ID, subfolder_name: str = None):
    """
    Verifica se um arquivo existe no Google Drive.
    """
    parent_id = drive_folder_id
    if subfolder_name:
        query_subfolder = f"'{drive_folder_id}' in parents and name='{subfolder_name}' and mimeType='application/vnd.google-apps.folder'"
        subfolder_response = drive_service.files().list(q=query_subfolder, fields="files(id)").execute()
        subfolders = subfolder_response.get('files', [])
        if not subfolders:
            return False
        parent_id = subfolders[0]['id']

    query_file = f"'{parent_id}' in parents and name='{nome_arquivo}' and mimeType != 'application/vnd.google-apps.folder'"
    try:
        response = drive_service.files().list(q=query_file, fields="files(id)").execute()
        files = response.get('files', [])
        return len(files) > 0
    except HttpError as error:
        print(f"[ERROR] Erro ao verificar existência de '{nome_arquivo}' no Drive: {error}")
        return False
    except Exception as e:
        print(f"[ERROR] Erro inesperado ao verificar existência de '{nome_arquivo}' no Drive: {e}")
        return False

def _excluir_arquivos_antigos(somente_lixeira=False):
    """Exclui somente arquivos criados há mais de 10 dias, preservando pastas."""
    limite = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)
    limite_drive = limite.isoformat().replace('+00:00', 'Z')
    query = (
        "'me' in owners and mimeType != 'application/vnd.google-apps.folder' "
        f"and createdTime < '{limite_drive}'"
    )
    if somente_lixeira:
        query += " and trashed = true"
    # Cada limpeza tem seu próprio transporte: o cliente global é usado pelos planos.
    service = build('drive', 'v3', credentials=creds)
    deleted_count = 0
    try:
        page_token = None
        candidatos = []
        # Finaliza a paginação antes de excluir, evitando alterar a lista paginada.
        while True:
            response = service.files().list(
                q=query, spaces='drive', fields='nextPageToken, files(id, createdTime)',
                pageToken=page_token
            ).execute()
            for arquivo in response.get('files', []):
                # Defesa adicional: datas ausentes ou inválidas nunca autorizam exclusão.
                try:
                    criado = datetime.datetime.fromisoformat(arquivo['createdTime'].replace('Z', '+00:00'))
                    if criado < limite:
                        candidatos.append(arquivo['id'])
                except (KeyError, ValueError, TypeError):
                    continue
            page_token = response.get('nextPageToken')
            if not page_token:
                break
        for file_id in candidatos:
            try:
                service.files().delete(fileId=file_id).execute()
                deleted_count += 1
            except HttpError as error:
                print(f"[ERROR] Falha ao excluir arquivo {file_id}: {error}")
        return deleted_count
    finally:
        service.close()


def deletar_todos_os_arquivos():
    """Exclui permanentemente arquivos com mais de 10 dias; preserva os recentes."""
    return _excluir_arquivos_antigos()


def esvaziar_lixeira_drive():
    """Exclui da lixeira somente arquivos com mais de 10 dias; preserva pastas."""
    return _excluir_arquivos_antigos(somente_lixeira=True)


def consultar_armazenamento_drive():
    """Consulta a cota da identidade autenticada sem usar o transporte dos planos."""
    service = build('drive', 'v3', credentials=creds)
    try:
        dados = service.about().get(fields='storageQuota').execute()
        quota = dados.get('storageQuota', {})
        def numero(campo):
            valor = quota.get(campo)
            return int(valor) if valor is not None else None
        limite = numero('limit')
        usado = numero('usage')
        percentual = round(usado / limite * 100, 2) if limite and usado is not None else None
        return {
            'capacity_bytes': limite,
            'used_bytes': usado,
            'drive_used_bytes': numero('usageInDrive'),
            'trash_used_bytes': numero('usageInDriveTrash'),
            'available_bytes': max(0, limite - usado) if limite is not None and usado is not None else None,
            'used_percent': percentual,
            'is_full': usado >= limite if limite is not None and usado is not None else None,
            'retention_days': 10,
            'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    finally:
        service.close()
