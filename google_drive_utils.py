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


def buscar_arquivo_personalizado_por_id_e_sku(target_id: str, sku: str, drive_folder_id: str = DEFAULT_FOLDER_ID):
    """
    Busca um arquivo no Google Drive que contenha o ID (ex: XXXX) e "Arquivo Personalizado"
    no nome, dentro da pasta especificada.
    Retorna o ID do arquivo e seu nome completo no Drive.
    """
    escaped_target_id = re.escape(target_id)
    query = f"'{drive_folder_id}' in parents and name contains '{target_id}' and name contains 'Arquivo Personalizado' and mimeType != 'application/vnd.google-apps.folder'"
    
    try:
        response = drive_service.files().list(q=query, fields="files(id, name)").execute()
        files = response.get('files', [])

        pattern = re.compile(rf"^{escaped_target_id}\s*-\s*Arquivo Personalizado.*\.dxf$", re.IGNORECASE)

        found_files = []
        for f in files:
            if pattern.match(f['name']):
                found_files.append(f)

        if not found_files:
            raise FileNotFoundError(f"Nenhum arquivo 'Arquivo Personalizado' com ID '{target_id}' encontrado no Drive.")
        
        return found_files[0]['id'], found_files[0]['name']

    except HttpError as error:
        raise Exception(f"Erro ao buscar arquivo personalizado para ID '{target_id}': {error}")
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

def deletar_todos_os_arquivos():
    """
    !!! CUIDADO: AÇÃO DESTRUTIVA E IRREVERSÍVEL !!!
    Exclui PERMANENTEMENTE todos os arquivos (NÃO pastas) que pertencem à conta de serviço,
    sem movê-los para a lixeira. Use com extrema cautela.
    Retorna o número de arquivos excluídos.
    """
    page_token = None
    deleted_count = 0
    print("[WARN] INICIANDO EXCLUSÃO IRREVERSÍVEL DE TODOS OS ARQUIVOS DA CONTA DE SERVIÇO.")
    
    try:
        while True:
            response = drive_service.files().list(
                q="'me' in owners and mimeType != 'application/vnd.google-apps.folder'",
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageToken=page_token
            ).execute()
            
            files = response.get('files', [])
            if not files and page_token is None:
                print("[INFO] Nenhum arquivo pertencente à conta de serviço foi encontrado.")
                break

            for file in files:
                try:
                    file_id = file.get('id')
                    file_name = file.get('name')
                    print(f"[INFO] Excluindo permanentemente: '{file_name}' (ID: {file_id})")
                    drive_service.files().delete(fileId=file_id).execute()
                    deleted_count += 1
                except HttpError as error:
                    print(f"[ERROR] Falha ao excluir o arquivo '{file_name}': {error}")
            
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break

        print(f"[SUCCESS] Limpeza concluída. Total de {deleted_count} arquivos excluídos permanentemente.")
        return deleted_count

    except HttpError as error:
        raise Exception(f"Erro de API durante a exclusão de arquivos: {error}")
    except Exception as e:
        raise Exception(f"Erro inesperado durante a limpeza total do Drive: {e}")

def esvaziar_lixeira_drive():
    """
    Esvazia permanentemente a lixeira do usuário (neste caso, a conta de serviço).
    """
    try:
        print("[INFO] Tentando esvaziar a lixeira do Google Drive...")
        drive_service.files().emptyTrash().execute()
        print("[INFO] Lixeira do Google Drive esvaziada com sucesso.")
    except HttpError as error:
        raise Exception(f"Erro ao esvaziar a lixeira do Drive: {error}")
    except Exception as e:
        raise Exception(f"Erro inesperado ao esvaziar a lixeira do Drive: {e}")

