# ============================================================
# 1️⃣ IMPORTS (સૌથી ઉપર)
# ============================================================
import streamlit as st
import cv2
import numpy as np
import json
import socket
import qrcode
import os
import shutil
import hashlib
import datetime
import tempfile
import faiss
import requests
import urllib.parse
import csv
import pandas as pd
import pickle
from insightface.app import FaceAnalysis
from face_search import find_best_global_assignment
from PIL import Image
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ============================================================
# 2️⃣ SESSION STATE INIT
# ============================================================
if "pending_faces" not in st.session_state:
    st.session_state.pending_faces = []
if "cart" not in st.session_state:
    st.session_state.cart = []
if "payment_done" not in st.session_state:
    st.session_state.payment_done = False

os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"

# ============================================================
# 3️⃣ CONSTANTS (ROOT_FOLDER_ID અહીં વ્યાખ્યાયિત કરો)
# ============================================================
ROOT_FOLDER_ID = "1B-qd1ZtJkQfxIUzpUCxdvaVIMAkVQtqH"  # <--- તમારો Shared Drive ફોલ્ડર ID

# ============================================================
# 4️⃣ GOOGLE DRIVE OAuth FUNCTION (લાઇન 48 પહેલાં આવવું જોઈએ)
# ============================================================
def get_drive_service():
    """OAuth 2.0 (st.secrets માંથી refresh_token) વાપરીને Drive service આપે છે"""
    try:
        creds = Credentials(
            token=None,
            refresh_token=st.secrets["oauth"]["refresh_token"],
            client_id=st.secrets["oauth"]["client_id"],
            client_secret=st.secrets["oauth"]["client_secret"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        
        return build('drive', 'v3', credentials=creds)
    
    except Exception as e:
        st.error(f"❌ OAuth Error: {e}")
        return None

# ============================================================
# 5️⃣ DRIVE HELPER & SYNC FUNCTIONS (Google Drive સાથે લિંક)
# ============================================================
def get_drive_folder_id(event_name):
    try:
        drive_service = get_drive_service()
        if drive_service is None:
            return None
        query = f"name = '{event_name}' and mimeType = 'application/vnd.google-apps.folder' and '{ROOT_FOLDER_ID}' in parents and trashed = false"
        results = drive_service.files().list(
            q=query, spaces='drive', fields='files(id, name)',
            supportsAllDrives=True, includeItemsFromAllDrives=True
        ).execute()
        items = results.get('files', [])
        if items:
            return items[0]['id']
        else:
            folder_metadata = {
                'name': event_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [ROOT_FOLDER_ID]
            }
            folder = drive_service.files().create(
                body=folder_metadata, fields='id', supportsAllDrives=True
            ).execute()
            return folder.get('id')
    except Exception as e:
        st.error(f"❌ Drive folder error: {e}")
        return None

def upload_to_drive(file_path, folder_id):
    try:
        drive_service = get_drive_service()
        if drive_service is None:
            return None
        file_metadata = {
            'name': os.path.basename(file_path),
            'parents': [folder_id]
        }
        media = MediaFileUpload(file_path, resumable=True)
        file = drive_service.files().create(
            body=file_metadata, media_body=media, fields='id', supportsAllDrives=True
        ).execute()
        return file.get('id')
    except Exception as e:
        st.error(f"❌ Drive upload error: {e}")
        return None

def save_event_data_to_drive(event_name, data, folder_id):
    try:
        drive_service = get_drive_service()
        if drive_service is None:
            return False
        temp_path = f"temp_{event_name}_data.json"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        query = f"name='data.json' and '{folder_id}' in parents and trashed=false"
        results = drive_service.files().list(
            q=query, fields="files(id)", supportsAllDrives=True
        ).execute()
        for file in results.get('files', []):
            drive_service.files().delete(fileId=file['id'], supportsAllDrives=True).execute()
        media = MediaFileUpload(temp_path, mimetype='application/json')
        file_metadata = {'name': 'data.json', 'parents': [folder_id]}
        drive_service.files().create(
            body=file_metadata, media_body=media, fields='id', supportsAllDrives=True
        ).execute()
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return True
    except Exception as e:
        st.error(f"❌ Drive save error: {e}")
        return False

# ============================================================
# 6️⃣ EVENT ડેટા લોડ અને લિસ્ટ (Drive + Local એકસાથે)
# ============================================================
def get_event_dir(event_name):
    base = "events"
    event_path = os.path.join(base, event_name)
    photos_path = os.path.join(event_path, "images")
    os.makedirs(photos_path, exist_ok=True)
    return event_path, photos_path

def save_event_data_local(event_name, data):
    try:
        event_path, _ = get_event_dir(event_name)
        json_path = os.path.join(event_path, "data.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        return False

# 🔥 ગૂગલ ડ્રાઈવમાંથી બધી જૂની ઇવેન્ટ્સનું લિસ્ટ મેળવવાનું ફંક્શન
def list_all_local_events():
    events_set = set()
    
    # ૧. પહેલા Google Drive પરથી બધી ઇવેન્ટ્સ શોધો
    try:
        drive_service = get_drive_service()
        if drive_service:
            query = f"'{ROOT_FOLDER_ID}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            results = drive_service.files().list(
                q=query, spaces='drive', fields='files(id, name)',
                supportsAllDrives=True, includeItemsFromAllDrives=True
            ).execute()
            for f in results.get('files', []):
                events_set.add(f['name'])
    except Exception:
        pass

    # ૨. જો લોકલ ફોલ્ડરમાં કોઈ ઇવેન્ટ હોય તો તે પણ ઉમેરો
    base = "events"
    if os.path.exists(base):
        for item in os.listdir(base):
            if os.path.isdir(os.path.join(base, item)):
                events_set.add(item)

    return sorted(list(events_set))

# 🔥 ગૂગલ ડ્રાઈવ + લોકલ બંનેમાંથી ડેટા લોડ કરવાનું ફંક્શન
def load_event_data_local(event_name):
    event_path, _ = get_event_dir(event_name)
    json_path = os.path.join(event_path, "data.json")
    
    # જો લોકલ ફાઈલ ના હોય (સર્વર રીસ્ટાર્ટ થયું હોય), તો Drive માંથી ડાઉનલોડ કરો
    if not os.path.exists(json_path):
        try:
            folder_id = get_drive_folder_id(event_name)
            if folder_id:
                drive_service = get_drive_service()
                query = f"name='data.json' and '{folder_id}' in parents and trashed=false"
                results = drive_service.files().list(
                    q=query, fields="files(id)", supportsAllDrives=True
                ).execute()
                files = results.get('files', [])
                if files:
                    file_id = files[0]['id']
                    content = drive_service.files().get_media(fileId=file_id).execute()
                    with open(json_path, "wb") as f:
                        f.write(content)
        except Exception:
            pass

    # હવે JSON વાંચો
    try:
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                data = {"password": "", "faces": data}
            for face in data.get("faces", []):
                if "embedding" in face and isinstance(face["embedding"], str):
                    try:
                        face["embedding"] = json.loads(face["embedding"])
                    except:
                        face["embedding"] = []
            return data
    except Exception:
        pass
        
    return {"password": "", "faces": []}

def parse_embedding(embedding_data):
    if embedding_data is None:
        return None
    if isinstance(embedding_data, str):
        try:
            return np.array(json.loads(embedding_data), dtype=np.float32)
        except:
            return None
    if isinstance(embedding_data, list):
        return np.array(embedding_data, dtype=np.float32)
    if isinstance(embedding_data, np.ndarray):
        return embedding_data
    return None

def load_event_data(event_name):
    return load_event_data_local(event_name)

def save_event_data(event_name, data):
    return save_event_data_local(event_name, data)

# ============================================================
# 7️⃣ INSIGHTFACE
# ============================================================
import os
import urllib.request
import zipfile
import insightface
from insightface.app import FaceAnalysis
import streamlit as st

MODEL_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
MODEL_DIR = os.path.expanduser("~/.insightface/models/buffalo_l")

def download_model():
    os.makedirs(os.path.dirname(MODEL_DIR), exist_ok=True)
    if not os.path.exists(MODEL_DIR):
        zip_path = os.path.join(os.path.dirname(MODEL_DIR), "buffalo_l.zip")
        urllib.request.urlretrieve(MODEL_URL, zip_path)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(os.path.dirname(MODEL_DIR))
        os.remove(zip_path)

@st.cache_resource
def load_insightface():
    download_model()
    app = FaceAnalysis(name='buffalo_l')
    app.prepare(ctx_id=-1, det_size=(640, 640))  # CPU માટે -1
    return app

app = load_insightface()
PHOTO_PRICE = 10

# ============================================================
# 8️⃣ PAGE CONFIG (st.set_page_config અહીં આવવું જોઈએ)
# ============================================================
st.set_page_config(page_title="જય ફોટો શોધ", page_icon="📸", layout="wide")

# ============================================================
# 9️⃣ TEST OAuth (હવે ફંક્શન્સ પછી)
# ============================================================
with st.expander("🧪 Test Google Drive Connection"):
    if st.button("Test Drive Connection"):
        service = get_drive_service()
        if service:
            st.success("✅ Drive service connected successfully!")
            try:
                results = service.files().list(
                    q=f"'{ROOT_FOLDER_ID}' in parents and trashed=false",
                    fields="files(id, name)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True
                ).execute()
                items = results.get('files', [])
                st.write(f"📁 Found {len(items)} items in Shared Drive")
                for item in items:
                    st.write(f"  - {item['name']} ({item['id']})")
            except Exception as e:
                st.error(f"❌ Shared Drive access error: {e}")
        else:
            st.error("❌ Drive service connection failed!")

# ============================================================
# CSS, HEADER, SIDEBAR (એ જ રાખો, વધુ નહીં લખું)
# ============================================================
st.markdown("""<style> ... તમારું CSS અહીં મૂકો ... </style>""", unsafe_allow_html=True)

col1, col2 = st.columns([1, 5])
with col1:
    try:
        st.image("assets/logo.jpg", width=100)
    except:
        st.markdown("## 📸")
with col2:
    st.markdown("""
    <div class="brand-text" style="display: flex; flex-direction: column; justify-content: center; height: 100%;">
        <h1 style="font-size: 2.8rem; font-weight: 900; color: #0f0f0f; margin: 0; letter-spacing: -1px;">
            JAY <span style="color: #d4af37;">PHOTO</span> SHODH
        </h1>
        <div style="font-size: 0.9rem; color: #6c757d; margin-top: -5px; font-weight: 400; letter-spacing: 2px;">
            ✨ AI POWERED PHOTO SEARCH
        </div>
    </div>
    """, unsafe_allow_html=True)

st.sidebar.image("assets/logo.jpg", width="stretch")
st.sidebar.markdown("""
<div style="text-align: center; padding: 0.5rem 0 1.5rem 0; border-bottom: 1px solid rgba(255,255,255,0.1); margin-bottom: 1.5rem;">
    <div style="color: white; font-weight: 800; font-size: 1.4rem; margin: 0; letter-spacing: 1px;">
        JAY <span style="color: #d4af37;">PHOTO</span>
    </div>
    <div style="color: #adb5bd; font-size: 0.7rem; font-weight: 400; letter-spacing: 3px; margin-top: 2px;">
        ART
    </div>
</div>
""", unsafe_allow_html=True)

option = st.sidebar.selectbox(
    "📌 પેજ પસંદ કરો",
    ["🔍 ફોટો શોધો", "📂 ઇવેન્ટ મેનેજ", "📱 QR કોડ બનાવો", "📊 Analytics", "📊 બેન્ચમાર્ક"],
    format_func=lambda x: x
)

# ============================================================
# PASSWORD PROTECTION (એડમિન)
# ============================================================
def check_password():
    if st.session_state.get("authenticated", False):
        return True
    st.sidebar.markdown("---")
    password = st.sidebar.text_input("🔒 એડમિન પાસવર્ડ:", type="password", key="admin_pass")
    if password:
        if password.strip() == "JayPhotoArt@2026":
            st.session_state.authenticated = True
            st.sidebar.success("✅ પ્રવેશ મળ્યો!")
            st.rerun()
            return True
        else:
            st.sidebar.error("❌ ખોટો પાસવર્ડ!")
            return False
    return False

# ============================================================
# TELEGRAM
# ============================================================
def send_telegram_message(message):
    try:
        TELEGRAM_BOT_TOKEN = st.secrets["telegram"]["bot_token"]
        TELEGRAM_CHAT_ID = st.secrets["telegram"]["chat_id"]
    except:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload)
        return response.status_code == 200
    except:
        return False

# ============================================================
# INSIGHTFACE & FAISS
# ============================================================
@st.cache_resource
def load_insightface():
    app = FaceAnalysis(name='buffalo_l', root='insightface_models')
    app.prepare(ctx_id=0, det_size=(640, 640))
    return app

@st.cache_resource
def load_event_faiss_index(event_name):
    data = load_event_data(event_name)
    if not data or not data.get("faces"):
        return None, None
    valid_faces = []
    for item in data.get("faces", []):
        emb = parse_embedding(item.get("embedding"))
        if emb is not None:
            valid_faces.append(item)
    if not valid_faces:
        return None, None
    embeddings = np.array([item["embedding"] for item in valid_faces], dtype=np.float32)
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    return index, valid_faces

def parse_embedding(embedding_data):
    if embedding_data is None:
        return None
    if isinstance(embedding_data, str):
        try:
            return np.array(json.loads(embedding_data), dtype=np.float32)
        except:
            return None
    if isinstance(embedding_data, list):
        return np.array(embedding_data, dtype=np.float32)
    if isinstance(embedding_data, np.ndarray):
        return embedding_data
    return None

app = load_insightface()
PHOTO_PRICE = 10

# ============================================================
# PAGE 1: MANAGE EVENTS (સુધારેલ)
# ============================================================
if option == "📂 ઇવેન્ટ મેનેજ":
    st.markdown("""
    <div class="card">
        <div class="card-title">📂 ઇવેન્ટ મેનેજમેન્ટ</div>
        <div class="card-desc">અહીં તમે નવી ઇવેન્ટ બનાવી શકો છો, ફોટા અપલોડ કરી શકો છો અને ચહેરાઓને લેબલ આપી શકો છો.</div>
    </div>
    """, unsafe_allow_html=True)

    with st.expander("➕ નવી ઇવેન્ટ બનાવો", expanded=False):
        new_event = st.text_input("ઇવેન્ટનું નામ (દા.ત., શર્મા_લગ્ન)")
        event_password = st.text_input("🔒 ઇવેન્ટ પાસવર્ડ (ગ્રાહકો માટે)", type="password")
        if st.button("📌 ઇવેન્ટ બનાવો", key="create_event"):
            if new_event.strip() and event_password.strip():
                event_name = new_event.strip()
                initial_data = {"password": event_password, "faces": []}
                if save_event_data_local(event_name, initial_data):
                    get_event_dir(event_name)
                    st.success(f"✅ ઇવેન્ટ '{event_name}' સફળતાપૂર્વક બની ગઈ!")
                    st.rerun()
                else:
                    st.error("❌ ઇવેન્ટ બનાવવામાં ભૂલ આવી.")
            else:
                st.error("❌ કૃપા કરીને નામ અને પાસવર્ડ બંને ભરો.")

    available_events = list_all_local_events()
    if not available_events:
        st.info("ℹ️ હજુ સુધી કોઈ ઇવેન્ટ નથી. ઉપર નવી ઇવેન્ટ બનાવો.")
    else:
        selected_event = st.selectbox("📁 ઇવેન્ટ પસંદ કરો", available_events)
        if selected_event:
            event_name_clean = str(selected_event).strip()
            folder_id = get_drive_folder_id(event_name_clean)
            
            if not folder_id:
                st.error("ગૂગલ ડ્રાઇવમાંથી ફોલ્ડર મળી શક્યું નથી. કૃપા કરીને Streamlit Secrets ચકાસો.")
       
            st.subheader(f"📸 ફોટા અપલોડ કરો - {selected_event}")
            uploaded_files = st.file_uploader(
                "ઇવેન્ટના ફોટા પસંદ કરો",
                type=['jpg', 'jpeg', 'png'],
                accept_multiple_files=True
            )
            # 🔥 ડીબગ લાઇન
            st.write("🔍 Checking conditions...")
            st.write(f"selected_event: {selected_event}")
            st.write(f"uploaded_files: {uploaded_files}")
            st.write(f"uploaded_files is not None: {uploaded_files is not None}")
            st.write(f"🔍 Uploaded files count: {len(uploaded_files) if uploaded_files else 0}")

            if uploaded_files:
                if st.button("🚀 ફોટા પ્રોસેસ અને સેવ કરો"):
                    # ---------- Drive Folder ID (એક વાર મેળવો) ----------
                    folder_id = get_drive_folder_id(selected_event.strip())

                    event_path, photos_path = get_event_dir(selected_event.strip())
                    if not os.path.exists(photos_path):
                        st.error("❌ ઇવેન્ટ ફોલ્ડર મળ્યું નહીં!")
                        st.stop()

                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    total_files = len(uploaded_files)

                    event_data = load_event_data_local(selected_event.strip())
                    existing_faces = event_data.get("faces", [])
                    processed_count = 0
                    auto_saved_count = 0

                    # ---------- એક જ લૂપ (બધું અંદર) ----------
                    for i, file in enumerate(uploaded_files):
                        status_text.text(f"⏳ {file.name} પર કામ ચાલુ છે... ({i+1}/{total_files})")

                        # ૧. ફોટો લોકલ સેવ કરો
                        file_path = os.path.join(photos_path, file.name)
                        file.seek(0)
                        with open(file_path, "wb") as f:
                            f.write(file.getvalue())

                        # ---------- Drive પર અપલોડ ----------
                        st.write(f"🔍 File: {file.name}, Path: {file_path}")
                        st.write(f"🔍 Folder ID: {folder_id}")

                        drive_file_id = upload_to_drive(file_path, folder_id)
                        st.write(f"🔍 Drive File ID: {drive_file_id}")  # <--- આ ઉમેરો


                        # ૨. ફેસ ડિટેક્શન
                        img = cv2.imread(file_path)
                        if img is None:
                            st.warning(f"⚠️ {file.name} વાંચવામાં ભૂલ આવી.")
                            continue

                        faces = app.get(img)
                        if len(faces) == 0:
                            st.warning(f"⚠️ {file.name} માં કોઈ ચહેરો મળ્યો નથી.")
                            continue

                        # ૩. દરેક ચહેરા માટે
                        for face_idx, face in enumerate(faces):
                            # ચહેરો ક્રોપ કરો
                            bbox = face.bbox.astype(int)
                            x1, y1, x2, y2 = bbox
                            pad = 20
                            h, w = img.shape[:2]
                            x1 = max(0, x1 - pad)
                            y1 = max(0, y1 - pad)
                            x2 = min(w, x2 + pad)
                            y2 = min(h, y2 + pad)
                            face_crop = img[y1:y2, x1:x2]
                            crop_filename = f"{hashlib.md5((file.name + str(face_idx)).encode()).hexdigest()[:8]}.jpg"
                            crop_path = os.path.join("temp_crops", crop_filename)
                            os.makedirs("temp_crops", exist_ok=True)
                            cv2.imwrite(crop_path, face_crop)

                            embedding = face.embedding / np.linalg.norm(face.embedding)

                            # --- SMART AUTO-LABEL ---
                            matched_label = None
                            best_sim = 0.0
                            if existing_faces:
                                for item in existing_faces:
                                    db_emb = parse_embedding(item.get("embedding"))
                                    if db_emb is not None:
                                        sim = float(np.dot(embedding, db_emb))
                                        if sim > 0.65 and sim > best_sim:
                                            best_sim = sim
                                            matched_label = item.get("person_label")

                            if matched_label and matched_label != "SKIP":
                                existing_faces.append({
                                    "filename": file.name,
                                    "drive_file_id": drive_file_id,
                                    "person_label": matched_label,
                                    "embedding": embedding.tolist()
                                })
                                auto_saved_count += 1
                                if os.path.exists(crop_path):
                                    os.remove(crop_path)
                            else:
                                st.session_state.pending_faces.append({
                                    "crop_path": crop_path,
                                    "embedding": embedding.tolist(),
                                    "original_filename": file.name,
                                    "drive_file_id": drive_file_id,
                                    "label": "SKIP"
                                })

                        processed_count += 1
                        progress_bar.progress((i + 1) / total_files)

                    # ---------- ૪. ડેટા સેવ કરો (લૂપની બહાર) ----------
                    event_data["faces"] = existing_faces

                    # લોકલ સેવ
                    save_event_data_local(selected_event.strip(), event_data)

                    # Drive સેવ (જો folder_id હોય તો)
                    if folder_id:
                        save_event_data_to_drive(selected_event.strip(), event_data, folder_id)
                    else:
                        st.warning("⚠️ Drive folder ID નથી, ફક્ત લોકલ સેવ થશે.")

                    status_text.empty()
                    st.success(f"✅ {processed_count} ફોટા સફળતાપૂર્વક પ્રોસેસ થયા! (ઓટો-સેવ: {auto_saved_count})")
                    st.rerun()

            # ---------- SMART GROUP LABELING ----------
            if st.session_state.pending_faces:
                folder_id = get_drive_folder_id(selected_event.strip())
                st.subheader(f"🏷️ {len(st.session_state.pending_faces)} નવા ચહેરાઓને સ્માર્ટ ગ્રૂપમાં ગોઠવો")
                st.caption("🔍 સમાન દેખાતા નવા ચહેરાઓ એક ગ્રૂપમાં ગોઠવાયા છે. નામ આપો:")

                pending = st.session_state.pending_faces
                embeddings = np.array([face["embedding"] for face in pending], dtype=np.float32)
                norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                norms[norms == 0] = 1
                embeddings_norm = embeddings / norms
                sim_matrix = np.dot(embeddings_norm, embeddings_norm.T)

                threshold = 0.65
                n = len(pending)
                visited = [False] * n
                clusters = []
                for i in range(n):
                    if not visited[i]:
                        cluster = [i]
                        visited[i] = True
                        for j in range(i+1, n):
                            if not visited[j] and sim_matrix[i][j] > threshold:
                                cluster.append(j)
                                visited[j] = True
                        clusters.append(cluster)

                for group_idx, cluster in enumerate(clusters):
                    st.markdown(f"### 🎯 ગ્રૂપ {group_idx + 1} (કુલ {len(cluster)} ચહેરા)")
                    cols = st.columns(min(4, len(cluster)))
                    for col_idx, face_idx in enumerate(cluster):
                        col = cols[col_idx % 4]
                        with col:
                            face_data = pending[face_idx]
                            if os.path.exists(face_data["crop_path"]):
                                st.image(face_data["crop_path"], width=150)
                            else:
                                st.warning("⚠️ ફોટો મળ્યો નથી")

                    group_label = st.text_input(
                        f"ગ્રૂપ {group_idx + 1} ને નામ આપો",
                        value="",
                        key=f"group_label_{group_idx}",
                        placeholder="દા.ત., રાજેશ, પ્રિયા, A"
                    )
                    if group_label.strip():
                        for face_idx in cluster:
                            pending[face_idx]["label"] = group_label.strip()
                    else:
                        for face_idx in cluster:
                            pending[face_idx]["label"] = "SKIP"
                    st.divider()

                if st.button("💾 બધા લેબલ સેવ કરો", key="save_all_labels"):
                    event_data = load_event_data_local(selected_event.strip())
                    existing_faces = event_data.get("faces", [])
                    count = 0
                    for face_data in pending:
                        lbl = face_data["label"].strip()
                        if lbl != "SKIP" and lbl != "":
                            existing_faces.append({
                                "filename": face_data["original_filename"],
                                "drive_file_id": face_data["drive_file_id"],
                                "person_label": lbl,
                                "embedding": face_data["embedding"]
                            })
                            count += 1

                    event_data["faces"] = existing_faces

                    # લોકલ સેવ
                    save_event_data_local(selected_event.strip(), event_data)

                    # Drive સેવ
                    if folder_id:
                        save_event_data_to_drive(selected_event.strip(), event_data, folder_id)
                    else:
                        st.warning("⚠️ Drive folder ID નથી, ફક્ત લોકલ સેવ થશે.")

                    # ક્રોપ ફાઈલો ડિલીટ કરો
                    for face_data in pending:
                        try:
                            if os.path.exists(face_data["crop_path"]):
                                os.remove(face_data["crop_path"])
                        except:
                            pass
                    st.session_state.pending_faces = []
                    st.cache_resource.clear()
                    st.success(f"✅ {count} નવા ચહેરા '{selected_event}' માં સેવ થયા!")
                    st.rerun()

                    # ક્રોપ ફાઈલો ડિલીટ કરો
                    for face_data in pending:
                        try:
                            if os.path.exists(face_data["crop_path"]):
                                os.remove(face_data["crop_path"])
                        except:
                            pass
                    st.session_state.pending_faces = []
                    st.cache_resource.clear()
                    st.success(f"✅ {count} નવા ચહેરા '{selected_event}' માં સેવ થયા!")
                    st.rerun()

            st.divider()
            event_data = load_event_data_local(selected_event.strip())
            faces_list = event_data.get("faces", [])
            st.write(f"📊 આ ઇવેન્ટમાં કુલ **{len(faces_list)}** લેબલ કરેલા ચહેરા છે.")
            if len(faces_list) > 0:
                st.subheader("🖼️ લેબલ કરેલા ફોટા")
                # Debug: Drive IDs ચકાસો
                st.write("🔍 Debug - Drive File IDs:")
                for i in range(0, len(faces_list), 4):
                    cols = st.columns(4)
                    for j, col in enumerate(cols):
                        idx = i + j
                        if idx < len(faces_list):
                            item = faces_list[idx]
                            file_id = item.get("drive_file_id")
                        with col:
                            try:
                                if file_id:
                                    # Google Drive થી ફોટો બતાવો
                                    img_url = f"https://drive.google.com/uc?export=view&id={file_id}"
                                    # img_url = f"https://drive.google.com/thumbnail?id={file_id}&sz=w200"
                                else:
                                    # કોઈ ફોટો ન મળે તો placeholder
                                    st.write(f"📁 {item.get('filename', 'Unknown')}")
                                    st.write(f"⚠️ Image not found (Drive ID: {file_id})")
                            except Exception as e:
                                 st.write(f"❌ Error loading image: {e}")
            else:
                st.info("ℹ️ હજુ સુધી કોઈ ફોટો લેબલ થયો નથી.")

            # DELETE EVENT
            st.divider()
            st.markdown("### 🗑️ ઇવેન્ટ કાઢી નાખો")
            st.warning(f"⚠️ આ ઇવેન્ટ ('{selected_event}') અને તેના બધા લોકલ ફોટા કાયમ માટે ડિલીટ થઈ જશે!")
            if st.button(f"🗑️ '{selected_event}' ઇવેન્ટ કાઢી નાખો", type="primary"):
                try:
                    shutil.rmtree(os.path.join("events", selected_event))
                    st.success(f"✅ '{selected_event}' ઇવેન્ટ લોકલ પરથી ડિલીટ થઈ ગઈ!")
                    st.session_state.pending_faces = []
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ ઇવેન્ટ ડિલીટ કરતી વખતે ભૂલ: {e}")

# ============================================================
# PAGE 2: SEARCH FACE (જેમ છે તેમ, માત્ર load/save લોકલ વાપરો)
# ============================================================
elif option == "🔍 ફોટો શોધો":
    matched_persons = set()
    query_params = st.query_params
    event_name = query_params.get("event", None)

    if event_name is None:
        st.markdown("""
        <div class="card">
            <div class="card-title">🔍 તમારા ફોટા શોધો</div>
            <div class="card-desc">કૃપા કરીને QR કોડ સ્કેન કરો અથવા ઇવેન્ટ લિંક ખોલો.</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        event_folder = os.path.join("events", event_name)
        if not os.path.exists(event_folder):
            st.error(f"❌ '{event_name}' ઇવેન્ટ મળી નહીં. કૃપા કરીને યોગ્ય QR કોડ વાપરો.")
        else:
            if f"auth_{event_name}" not in st.session_state:
                st.session_state[f"auth_{event_name}"] = False

            if not st.session_state[f"auth_{event_name}"]:
                st.markdown(f"""
                <div class="card">
                    <div class="card-title">🔒 '{event_name}' ઇવેન્ટ માટે પાસવર્ડ</div>
                    <div class="card-desc">આ ઇવેન્ટને ઍક્સેસ કરવા માટે પાસવર્ડ લખો.</div>
                </div>
                """, unsafe_allow_html=True)
                entered_password = st.text_input("🔑 ઇવેન્ટ પાસવર્ડ:", type="password")
                if st.button("🚪 પ્રવેશ કરો"):
                    event_data = load_event_data_local(event_name)
                    if event_data.get("password") == entered_password:
                        st.session_state[f"auth_{event_name}"] = True
                        st.success("✅ પ્રવેશ મળ્યો!")
                        st.rerun()
                    else:
                        st.error("❌ ખોટો પાસવર્ડ!")
                st.stop()

            st.markdown(f"""
            <div class="card">
                <div class="card-title">🔍 '{event_name}' માં તમારા ફોટા શોધો</div>
                <div class="card-desc">નીચે તમારો ફોટો અપલોડ કરો અથવા સેલ્ફી લો, અમે તમારા બધા ફોટા શોધી આપીશું.</div>
            </div>
            """, unsafe_allow_html=True)

            index, db_data = load_event_faiss_index(event_name)
            if index is None or len(db_data) == 0:
                st.warning("ℹ️ આ ઇવેન્ટમાં હજુ સુધી કોઈ ફોટા નથી.")
            else:
                unique_labels = set()
                for item in db_data:
                    unique_labels.add(item["person_label"])
                persons_list = list(unique_labels)
                st.sidebar.success(f"✅ {len(db_data)} ચહેરા ઇન્ડેક્સ થયા")
                st.sidebar.info(f"👤 વ્યક્તિઓ: {', '.join(persons_list)}")

                        # ---------- 2 OPTIONS: Selfie / Upload ----------
        # ---------- 2 OPTIONS: Selfie / Upload ----------
        st.subheader("📸 ફોટો અપલોડ કરવાની રીત")
        upload_option = st.radio(
            "વિકલ્પ પસંદ કરો:",
            ["📸 કેમેરાથી સેલ્ફી લો", "📁 ફોટો અપલોડ કરો"],
            index=0,
            key=f"upload_option_{event_name}"
        )

        uploaded_file = None
        if upload_option == "📸 કેમેરાથી સેલ્ફી લો":
            uploaded_file = st.camera_input("📸 સેલ્ફી લો", key=f"camera_input_{event_name}")
        else:
            uploaded_file = st.file_uploader(
                "📁 ફોટો પસંદ કરો...",
                type=["jpg", "jpeg", "png"],
                key=f"file_uploader_{event_name}"
            )

        matched_photos = []

        # ---------- SEARCH PROCESS (બધા ફોટા બતાવવા માટે) ----------
        if uploaded_file is not None:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_file:
                tmp_file.write(uploaded_file.getvalue())
                tmp_path = tmp_file.name

            img = cv2.imread(tmp_path)
            if img is not None:
                st.image(img, channels="BGR", caption="તમારો ફોટો", width=250)
                with st.spinner("🔍 તમારા બધા ફોટા શોધાઈ રહ્યા છે..."):
                    faces = app.get(img)
                    if len(faces) == 0:
                        st.warning("❌ ફોટામાં કોઈ ચહેરો ઓળખાયો નહીં! કૃપા કરીને ચોખ્ખો ફોટો આપો.")
                    else:
                        st.success("✅ ચહેરો ઓળખાઈ ગયો!")
                        user_face = faces[0]
                        user_emb = user_face.embedding / np.linalg.norm(user_face.embedding)

                        # ડેટાબેઝના ૧૩ એ ૧૩ ફોટા સાથે સરખામણી કરો
                        matched_person_names = set()
                        for item in db_data:
                            db_emb = parse_embedding(item.get("embedding"))
                            if db_emb is not None:
                                sim = float(np.dot(user_emb, db_emb))
                                if sim >= 0.40:  # ૪૦% થી વધુ મેચ થતા બધા જ ફોટા લો
                                    item_copy = dict(item)
                                    item_copy["similarity"] = sim
                                    matched_photos.append(item_copy)
                                    if item.get("person_label"):
                                        matched_person_names.add(item.get("person_label"))

                        # શ્રેષ્ઠ મેચ પહેલાં ગોઠવો
                        matched_photos = sorted(matched_photos, key=lambda x: x["similarity"], reverse=True)

                        # ---------- ફોટા બતાવો ----------
                        st.subheader("📸 તમારા મેચ થયેલા બધા ફોટા")
                        if matched_photos:
                            st.success(f"🎉 તમને મળતા આવતા કુલ **{len(matched_photos)}** ફોટા મળ્યા છે!")
                            
                            cols = st.columns(3)
                            for idx, item in enumerate(matched_photos):
                                col = cols[idx % 3]
                                with col:
                                    file_id = item.get("drive_file_id")
                                    img_path = None
                                    
                                    if file_id:
                                        img_url = f"https://drive.google.com/thumbnail?id={file_id}&sz=w600"
                                        st.image(img_url, caption=f"મેચ: {int(item['similarity']*100)}%", use_container_width=True)
                                        img_path = img_url
                                    else:
                                        local_path = os.path.join("events", event_name, "images", item.get("filename", ""))
                                        if os.path.exists(local_path):
                                            st.image(local_path, caption=f"મેચ: {int(item['similarity']*100)}%", use_container_width=True)
                                            img_path = local_path
                                        else:
                                            st.write(f"📁 {item.get('filename', 'Unknown')}")

                                    price = PHOTO_PRICE
                                    person_name = item.get("person_label", "MyPhoto")
                                    cart_key = f"cart_{idx}_{item.get('filename')}"
                                    
                                    selected = st.checkbox(f"🛒 ₹{price}" if price > 0 else "🆓 FREE", key=cart_key)
                                    
                                    current_item = {
                                        "person": person_name,
                                        "filename": item.get("filename"),
                                        "price": price,
                                        "img_path": img_path,
                                        "drive_file_id": file_id
                                    }
                                    if selected:
                                        if not any(c["filename"] == item.get("filename") for c in st.session_state.cart):
                                            st.session_state.cart.append(current_item)
                                    else:
                                        st.session_state.cart = [c for c in st.session_state.cart if c["filename"] != item.get("filename")]

                            if st.button(f"➕ આ બધા ({len(matched_photos)}) ફોટા કાર્ટમાં ઉમેરો", key=f"add_all_matched"):
                                for item in matched_photos:
                                    file_id = item.get("drive_file_id")
                                    local_path = os.path.join("events", event_name, "images", item.get("filename", ""))
                                    c_item = {
                                        "person": item.get("person_label", "MyPhoto"),
                                        "filename": item.get("filename"),
                                        "price": PHOTO_PRICE,
                                        "img_path": local_path if os.path.exists(local_path) else None,
                                        "drive_file_id": file_id
                                    }
                                    if not any(c["filename"] == item.get("filename") for c in st.session_state.cart):
                                        st.session_state.cart.append(c_item)
                                st.rerun()
                        else:
                            st.warning("⚠️ તમારો મેળ ખાતો કોઈ ફોટો મળ્યો નથી.")

        # ============================================================
        # 🛒 CART DISPLAY & RAZORPAY PAYMENT (સાઇડબાર)
        # ============================================================
        st.sidebar.markdown("---")
        st.sidebar.markdown("## 🛒 તમારું કાર્ટ")
        
        if st.session_state.cart:
            cart = st.session_state.cart
            total_price = sum(item.get("price", PHOTO_PRICE) for item in cart)
            
            for idx, item in enumerate(cart):
                price = item.get("price", PHOTO_PRICE)
                if price == 0:
                    st.sidebar.write(f"{idx+1}. {item['person']} - 🆓 FREE")
                else:
                    st.sidebar.write(f"{idx+1}. {item['person']} - ₹{price}")
            
            st.sidebar.markdown(f"### 💰 કુલ રકમ: ₹{total_price}")

            # કાર્ટ ખાલી કરો
            if st.sidebar.button("🗑️ કાર્ટ ખાલી કરો", key="clear_cart_btn"):
                st.session_state.cart = []
                st.session_state.payment_done = False
                st.session_state.show_checkout = False
                st.session_state.razorpay_url = None
                st.rerun()

            is_ready_to_download = (total_price == 0) or st.session_state.get("payment_done", False)

            # ૧. જો પૈસા ચૂકવવાના હોય (total_price > 0)
            if total_price > 0 and not st.session_state.get("payment_done", False):
                if st.sidebar.button(f"🧾 ચેકઆઉટ કરો (₹{total_price})", key="checkout_btn"):
                    st.session_state.show_checkout = True
                    
                    # 🔥 Razorpay Payment Link બનાવો
                    try:
                        rz_key = st.secrets["razorpay_key_id"]
                        rz_secret = st.secrets["razorpay_key_secret"]
                        amount_paise = int(float(total_price) * 100)
                        
                        resp = requests.post(
                            "https://api.razorpay.com/v1/payment_links",
                            auth=(rz_key, rz_secret),
                            json={
                                "amount": amount_paise,
                                "currency": "INR",
                                "accept_partial": False,
                                "description": f"Photo Download for {event_name}",
                                "customer": {
                                    "name": "Customer",
                                    "email": "customer@example.com",
                                    "contact": "+919999999999"
                                },
                                "notify": {"sms": False, "email": False},
                                "reminder_enable": False,
                                "callback_url": "https://jayphotoart.streamlit.app",
                                "callback_method": "get"
                            }
                        )
                        if resp.status_code == 200:
                            st.session_state.razorpay_url = resp.json().get("short_url")
                        else:
                            st.session_state.razorpay_url = None
                    except Exception as e:
                        st.sidebar.error(f"Razorpay Error: {e}")

                # Razorpay બટન બતાવો
                if st.session_state.get("show_checkout", False):
                    st.sidebar.markdown("---")
                    st.sidebar.markdown(f"### 💳 પેમેન્ટ કરો: ₹{total_price}")
                    
                    rz_url = st.session_state.get("razorpay_url")
                    if rz_url:
                        st.sidebar.link_button("💳 Pay via Razorpay (GPay/Card/UPI)", rz_url, use_container_width=True)
                    else:
                        # જો API કનેક્ટ ના હોય તો સાદો QR
                        MY_UPI_ID = "dineshmakwna123@oksbi"
                        upi_qr_url = f"upi://pay?pa={MY_UPI_ID}&pn=JayPhotography&am={float(total_price):.2f}&cu=INR&tn=PhotoDownload"
                        pay_qr = qrcode.make(upi_qr_url)
                        pay_qr_arr = np.array(pay_qr.convert('RGB'))
                        st.sidebar.image(pay_qr_arr, caption="📱 UPI થી સ્કેન કરો", width=180)

                    if st.sidebar.button("✅ પેમેન્ટ થઈ ગયું! (ફોટા મેળવો)", key="payment_done_btn", use_container_width=True):
                        unique_persons = set(item['person'] for item in cart)
                        persons_text = ", ".join(unique_persons)
                        send_telegram_message(
                            f"💰 <b>પેમેન્ટ મળ્યું!</b>\n"
                            f"📸 ઇવેન્ટ: {event_name}\n"
                            f"👤 ગ્રાહક: {persons_text}\n"
                            f"💵 રકમ: ₹{total_price}\n"
                            f"🕒 {datetime.datetime.now().strftime('%d-%m-%Y %H:%M')}"
                        )
                        st.session_state.payment_done = True
                        st.session_state.show_checkout = False
                        st.rerun()

            # ૨. ડાઉનલોડ અને શેરિંગ બટનો (સાચી ZIP ફાઈલ બનશે)
            if is_ready_to_download:
                st.sidebar.markdown("---")
                st.sidebar.markdown("## 📥 તમારા ફોટા ડાઉનલોડ કરો")
                
                import zipfile, io
                zip_buffer = io.BytesIO()
                has_files = False
                
                with zipfile.ZipFile(zip_buffer, "w") as zip_file:
                    for item in cart:
                        file_id = item.get("drive_file_id")
                        filename = item.get("filename", "photo.jpg")
                        file_bytes = None
                        
                        if file_id:
                            try:
                                d_url = f"https://drive.google.com/uc?export=download&id={file_id}"
                                res = requests.get(d_url, timeout=10)
                                if res.status_code == 200:
                                    file_bytes = res.content
                            except:
                                pass
                        
                        if not file_bytes:
                            local_p = os.path.join("events", event_name, "images", filename)
                            if os.path.exists(local_p):
                                with open(local_p, "rb") as f:
                                    file_bytes = f.read()
                                    
                        if file_bytes:
                            zip_file.writestr(filename, file_bytes)
                            has_files = True
                
                zip_buffer.seek(0)
                if has_files:
                    st.sidebar.download_button(
                        label="📥 બધા ફોટા ડાઉનલોડ કરો (ZIP)",
                        data=zip_buffer,
                        file_name=f"{event_name}_photos.zip",
                        mime="application/zip",
                        key="zip_download_final"
                    )
                
                # એક-એક ફોટો ડાઉનલોડ
                for idx, item in enumerate(cart):
                    file_id = item.get("drive_file_id")
                    filename = item.get("filename", f"photo_{idx+1}.jpg")
                    if file_id:
                        d_link = f"https://drive.google.com/uc?export=download&id={file_id}"
                        st.sidebar.markdown(f"📸 [{filename} ડાઉનલોડ કરો]({d_link})")

                # શેરિંગ બટન્સ
                st.sidebar.markdown("---")
                st.sidebar.markdown("## 📤 તમારા ફોટા શેર કરો")
                app_url = "https://jayphotofinder.streamlit.app"
                share_text = "🌟 મારા ઇવેન્ટના સુંદર ફોટા જુઓ! જય ફોટો શોધ દ્વારા શોધ્યા."
                whatsapp_url = f"https://api.whatsapp.com/send?text={share_text} {app_url}"
                st.sidebar.markdown(f"[![WhatsApp](https://img.icons8.com/color/48/000000/whatsapp.png)]({whatsapp_url}) શેર કરો")
                
                if st.sidebar.button("📋 લિંક કોપી કરો", key="copy_link_btn"):
                    st.sidebar.code(app_url)
                    st.sidebar.success("✅ લિંક કોપી થઈ ગઈ!")

                if st.sidebar.button("✅ કામ પૂરું થયું! (કાર્ટ ખાલી કરો)", key="clear_after_download_btn"):
                    st.session_state.cart = []
                    st.session_state.payment_done = False
                    st.session_state.show_checkout = False
                    st.rerun()

        else:
            st.sidebar.info("🛒 કાર્ટ ખાલી છે")
            st.session_state.payment_done = False
            st.session_state.show_checkout = False

# ============================================================
# PAGE 3: QR CODE GENERATE (એ જ)
# ============================================================
elif option == "📱 QR કોડ બનાવો":
    st.markdown("""
    <div class="card">
        <div class="card-title">📱 QR કોડ બનાવો</div>
        <div class="card-desc">અહીં તમે કોઈ પણ ઇવેન્ટ માટે QR કોડ બનાવી શકો છો. ગ્રાહકો આ QR કોડ સ્કેન કરીને તેમના ફોટા શોધી શકશે.</div>
    </div>
    """, unsafe_allow_html=True)

    events = list_all_local_events()
    if not events:
        st.warning("⚠️ હજુ સુધી કોઈ ઇવેન્ટ નથી. કૃપા કરીને '📂 ઇવેન્ટ મેનેજ' માં પહેલાં ઇવેન્ટ બનાવો.")
    else:
        selected_event = st.selectbox("📂 ઇવેન્ટ પસંદ કરો", events)
        if selected_event:
            clean_event = selected_event.replace(" ", "_")
            url = f"https://jayphotoart.streamlit.app/?event={urllib.parse.quote(clean_event)}"
            qr_img = qrcode.make(url)
            qr_img_array = np.array(qr_img.convert('RGB'))
            col1, col2 = st.columns([1, 1])
            with col1:
                st.image(qr_img_array, caption=f"📱 '{selected_event}' માટે QR કોડ", width=300)
                st.success(f"🔗 URL: {url}")
                st.caption("📌 ગ્રાહકો આ QR કોડ સ્કેન કરીને તેમના ફોટા જોઈ શકે છે.")
                from io import BytesIO
                buffered = BytesIO()
                qr_img.save(buffered, format="PNG")
                st.download_button(
                    label="⬇ QR કોડ ડાઉનલોડ કરો",
                    data=buffered.getvalue(),
                    file_name=f"qr_{clean_event}.png",
                    mime="image/png"
                )
            with col2:
                st.info("💡 કેવી રીતે વાપરવું?")
                st.write("1. આ QR કોડને પ્રિન્ટ કરીને ઇવેન્ટમાં મૂકો.")
                st.write("2. ગ્રાહકો ફોન વડે સ્કેન કરશે.")
                st.write("3. તેઓ સેલ્ફી લઈને તેમના ફોટા જોશે.")

# ============================================================
# PAGE 4: BENCHMARK (એ જ)
# ============================================================
else:
    st.header("📊 બેન્ચમાર્ક પરિણામો")
    try:
        df = pd.read_csv("benchmark_results.csv")
        st.dataframe(df)
        col1, col2, col3 = st.columns(3)
        top1_pass = (df['top1'] == "PASS").sum()
        exact_pass = (df['exact_ranking'] == "PASS").sum()
        avg_rank = df['ranking_accuracy'].mean()
        col1.metric("Top-1 Accuracy", f"{top1_pass}/9 ({top1_pass/9*100:.1f}%)")
        col2.metric("Exact Ranking", f"{exact_pass}/9 ({exact_pass/9*100:.1f}%)")
        col3.metric("Avg Rank Score", f"{avg_rank:.1f}%")
        st.bar_chart(df.set_index('test')['ranking_accuracy'])
    except FileNotFoundError:
        st.warning("benchmark_results.csv મળી નહીં.")

# ============================================================
# FOOTER
# ============================================================
st.markdown("""
<div class="footer">
    📸 <strong>જય ફોટો શોધ</strong> - AI દ્વારા તમારા ફોટા શોધો<br>
    © 2026 Jay Photography | Made with ❤️ in Gujarat
</div>
""", unsafe_allow_html=True)