# ============================================================
# 1️⃣ IMPORTS
# ============================================================
import streamlit as st
import cv2
import numpy as np
import json
import qrcode
import os
import shutil
import hashlib
import datetime
import tempfile
import requests
import urllib.parse
from PIL import Image
from insightface.app import FaceAnalysis
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==========================================
# 🔐 LOAD CONFIGURATION & SECRETS SAFELY
# ==========================================

try:
    # 1. Admin Password
    ADMIN_PASSWORD = st.secrets["admin_password"]

    # 2. OAuth Details (Dictionary)
    OAUTH_CONFIG = st.secrets["oauth"]
    OAUTH_CLIENT_ID = OAUTH_CONFIG["client_id"]
    OAUTH_CLIENT_SECRET = OAUTH_CONFIG["client_secret"]

    # 3. GCP Service Account (Credentials Dict)
    # GCP લાઈબ્રેરી dict સ્વીકારે છે, તેથી તેને dict ફોર્મેટમાં લોડ કર્યું છે
    GCP_CREDENTIALS_DICT = dict(st.secrets["gcp_service_account"])

    # 4. Telegram Details
    TELEGRAM_CONFIG = st.secrets["telegram"]
    TELEGRAM_BOT_TOKEN = TELEGRAM_CONFIG["bot_token"]
    TELEGRAM_CHAT_ID = TELEGRAM_CONFIG["chat_id"]

    # 5. Razorpay Test Credentials
    RAZORPAY_KEY_ID = st.secrets["razorpay_key_id"]
    RAZORPAY_KEY_SECRET = st.secrets.get("razorpay_key_secret", "")
    
except KeyError as e:
    st.error(f"⚠️ Secrets.toml માં કી ખૂટે છે: {e}")
    st.info("કૃપા કરીને .streamlit/secrets.toml ફાઈલ યોગ્ય રીતે સેટ કરો.")
    st.stop()
#===========================================================
import streamlit as st
import razorpay

# ==========================================
# 🔐 RAZORPAY CLIENT INITIALIZATION
# ==========================================
try:
    RAZORPAY_KEY_ID = st.secrets["razorpay_key_id"]
    RAZORPAY_KEY_SECRET = st.secrets["razorpay_key_secret"]
    
    # અહીં razorpay_client વ્યાખ્યાયિત થાય છે
    razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
except Exception as e:
    st.error(f"Razorpay Setup માં ભૂલ છે: {e}")
    st.info("કૃપા કરીને .streamlit/secrets.toml માં razorpay_key_id અને razorpay_key_secret ચેક કરો.")
    st.stop()
    


# ============================================================
# 2️⃣ SESSION STATE INIT
# ============================================================
if "pending_faces" not in st.session_state:
    st.session_state.pending_faces = []
if "cart" not in st.session_state:
    st.session_state.cart = []
if "payment_done" not in st.session_state:
    st.session_state.payment_done = False
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False
if "show_checkout" not in st.session_state:
    st.session_state.show_checkout = False

os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
ROOT_FOLDER_ID = "1B-qd1ZtJkQfxIUzpUCxdvaVIMAkVQtqH"
PHOTO_PRICE = 10

# ============================================================
# 3️⃣ TELEGRAM NOTIFICATION FUNCTION
# ============================================================
def send_telegram_message(message):
    try:
        token = st.secrets.get("telegram", {}).get("bot_token")
        chat_id = st.secrets.get("telegram", {}).get("chat_id")
        if not token or not chat_id:
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        resp = requests.post(url, json=payload, timeout=8)
        return resp.status_code == 200
    except Exception:
        return False

# ============================================================
# 4️⃣ GOOGLE DRIVE OAuth & HELPER FUNCTIONS
# ============================================================
def get_drive_service():
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
    except Exception:
        return None

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
    except Exception:
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
    except Exception:
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
    except Exception:
        return False

# ============================================================
# 5️⃣ EVENT DATA SYNC FUNCTIONS
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
    except Exception:
        return False

def list_all_local_events():
    events_set = set()
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

    base = "events"
    if os.path.exists(base):
        for item in os.listdir(base):
            if os.path.isdir(os.path.join(base, item)):
                events_set.add(item)

    return sorted(list(events_set))

def load_event_data_local(event_name):
    event_path, _ = get_event_dir(event_name)
    json_path = os.path.join(event_path, "data.json")
    
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

# ============================================================
# 6️⃣ INSIGHTFACE MODEL
# ============================================================
@st.cache_resource
def load_insightface():
    app = FaceAnalysis(name='buffalo_l', root='insightface_models')
    app.prepare(ctx_id=0, det_size=(640, 640))
    return app

app = load_insightface()

# ============================================================
# 7️⃣ PAGE CONFIG & HEADER
# ============================================================
st.set_page_config(page_title="જય ફોટો શોધ", page_icon="📸", layout="wide")

col1, col2 = st.columns([1, 5])
with col1:
    try:
        st.image("assets/logo.jpg", width=100)
    except:
        st.markdown("## 📸")
with col2:
    st.markdown("""
    <div style="display: flex; flex-direction: column; justify-content: center; height: 100%;">
        <h1 style="font-size: 2.5rem; font-weight: 900; color: #0f0f0f; margin: 0;">
            JAY <span style="color: #d4af37;">PHOTO</span> SHODH
        </h1>
        <div style="font-size: 0.85rem; color: #6c757d; letter-spacing: 2px;">
            ✨ AI POWERED PHOTO SEARCH
        </div>
    </div>
    """, unsafe_allow_html=True)

# ============================================================
# 8️⃣ SMART NAVIGATION (ગ્રાહક અને એડમિન માટે અલગ રસ્તા)
# ============================================================
query_params = st.query_params
event_name_from_url = query_params.get("event")

# જો QR સ્કેન દ્વારા ગ્રાહક આવે તો ફક્ત કસ્ટમર વ્યૂ બતાવો
if event_name_from_url:
    is_client_mode = True
else:
    is_client_mode = False

# સાઇડબાર મેનૂ
if is_client_mode:
    option = "🔍 ફોટો શોધો"
    st.sidebar.info("📱 ગ્રાહક ફોટો શોધ મોડ")
else:
    if st.session_state.admin_logged_in:
        option = st.sidebar.selectbox("📌 એડમિન મેનૂ", ["📂 ઇવેન્ટ મેનેજ", "📱 QR કોડ બનાવો", "🔍 ફોટો શોધો ટેસ્ટિંગ"])
        if st.sidebar.button("🚪 એડમિન લૉગઆઉટ"):
            st.session_state.admin_logged_in = False
            st.rerun()
    else:
        option = "🔒 એડમિન લૉગિન"

# ============================================================
# 🔒 ADMIN LOGIN PAGE (એપ ખુલતાં જ સૌથી પહેલાં પાસવર્ડ પૂછશે)
# ============================================================
if option == "🔒 એડમિન લૉગિન":
    st.markdown("---")
    st.markdown("### 🔒 એડમિન પેનલ લૉગિન")
    st.caption("ઇવેન્ટ મેનેજ કરવા અથવા QR કોડ બનાવવા માટે એડમિન પાસવર્ડ નાખો.")
    
    admin_input = st.text_input("🔑 એડમિન પાસવર્ડ:", type="password", key="main_admin_pass")
    
    if st.button("🚪 પ્રવેશ કરો", key="main_admin_login_btn"):
        correct_password = st.secrets.get("admin_password")
        if admin_input.strip() == correct_password.strip():
            st.session_state.admin_logged_in = True
            st.success("✅ એડમિન લૉગિન સફળ!")
            st.rerun()
        else:
            st.error("❌ ખોટો એડમિન પાસવર્ડ!")

# ============================================================
# PAGE 1: MANAGE EVENTS (માત્ર એડમિન માટે)
# ============================================================
elif option == "📂 ઇવેન્ટ મેનેજ":
    if not st.session_state.admin_logged_in:
        st.error("❌ આ પેજ માટે એડમિન લૉગિન જરૂરી છે.")
        st.stop()

    st.markdown("### 📂 ઇવેન્ટ મેનેજમેન્ટ")

    with st.expander("➕ નવી ઇવેન્ટ બનાવો", expanded=False):
        new_event = st.text_input("ઇવેન્ટનું નામ (દા.ત., શર્મા_લગ્ન)")
        event_password = st.text_input("🔒 ઇવેન્ટ પાસવર્ડ (ગ્રાહકો માટે)", type="password")
        if st.button("📌 ઇવેન્ટ બનાવો", key="create_event"):
            if new_event.strip() and event_password.strip():
                event_name = new_event.strip()
                initial_data = {"password": event_password, "faces": []}
                folder_id = get_drive_folder_id(event_name)
                save_event_data_local(event_name, initial_data)
                if folder_id:
                    save_event_data_to_drive(event_name, initial_data, folder_id)
                st.success(f"✅ ઇવેન્ટ '{event_name}' સફળતાપૂર્વક બની ગઈ!")
                st.rerun()
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
            
            event_data = load_event_data_local(event_name_clean)
            existing_faces = event_data.get("faces", [])
            st.info(f"📊 આ ઇવેન્ટમાં અત્યારે કુલ **{len(existing_faces)}** ફોટા સેવ છે.")
       
            st.subheader(f"📸 નવા ફોટા ઉમેરો - {selected_event}")
            uploaded_files = st.file_uploader(
                "ઇવેન્ટના ફોટા પસંદ કરો",
                type=['jpg', 'jpeg', 'png'],
                accept_multiple_files=True
            )

            if uploaded_files:
                if st.button("🚀 ફોટા પ્રોસેસ અને સેવ કરો"):
                    folder_id = get_drive_folder_id(selected_event.strip())
                    event_path, photos_path = get_event_dir(selected_event.strip())

                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    total_files = len(uploaded_files)

                    event_data = load_event_data_local(selected_event.strip())
                    existing_faces = event_data.get("faces", [])
                    processed_count = 0
                    auto_saved_count = 0

                    for i, file in enumerate(uploaded_files):
                        status_text.text(f"⏳ {file.name} પર કામ ચાલુ છે... ({i+1}/{total_files})")

                        file_path = os.path.join(photos_path, file.name)
                        file.seek(0)
                        with open(file_path, "wb") as f:
                            f.write(file.getvalue())

                        drive_file_id = upload_to_drive(file_path, folder_id)

                        img = cv2.imread(file_path)
                        if img is None:
                            continue

                        faces = app.get(img)
                        if len(faces) == 0:
                            continue

                        for face_idx, face in enumerate(faces):
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

                    event_data["faces"] = existing_faces
                    save_event_data_local(selected_event.strip(), event_data)
                    if folder_id:
                        save_event_data_to_drive(selected_event.strip(), event_data, folder_id)

                    st.cache_resource.clear()
                    status_text.empty()
                    st.success(f"✅ {processed_count} નવા ફોટા ઉમેરાઈ ગયા! (ઓટો-સેવ: {auto_saved_count})")
                    st.rerun()

            # SMART GROUP LABELING
            if st.session_state.pending_faces:
                folder_id = get_drive_folder_id(selected_event.strip())
                st.subheader(f"🏷️ {len(st.session_state.pending_faces)} નવા ચહેરાઓને નામ આપો")

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

                    group_label = st.text_input(
                        f"ગ્રૂપ {group_idx + 1} ને નામ આપો",
                        value="",
                        key=f"group_label_{group_idx}",
                        placeholder="દા.ત., રાજેશ, પ્રિયા"
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
                    save_event_data_local(selected_event.strip(), event_data)
                    if folder_id:
                        save_event_data_to_drive(selected_event.strip(), event_data, folder_id)

                    for face_data in pending:
                        try:
                            if os.path.exists(face_data["crop_path"]):
                                os.remove(face_data["crop_path"])
                        except:
                            pass
                            
                    st.session_state.pending_faces = []
                    st.cache_resource.clear()
                    st.success(f"✅ {count} નવા ચહેરા સફળતાપૂર્વક ઉમેરાઈ ગયા!")
                    st.rerun()

# ============================================================
# PAGE 2: QR CODE GENERATE (માત્ર એડમિન માટે)
# ============================================================
elif option == "📱 QR કોડ બનાવો":
    if not st.session_state.admin_logged_in:
        st.error("❌ આ પેજ માટે એડમિન લૉગિન જરૂરી છે.")
        st.stop()

    st.markdown("### 📱 QR કોડ બનાવો")
    events = list_all_local_events()
    if not events:
        st.warning("⚠️ હજુ સુધી કોઈ ઇવેન્ટ નથી. કૃપા કરીને '📂 ઇવેન્ટ મેનેજ' માં પહેલાં ઇવેન્ટ બનાવો.")
    else:
        selected_event = st.selectbox("📂 ઇવેન્ટ પસંદ કરો", events)
        if selected_event:
            clean_event = selected_event.strip()
            url = f"https://jayphotoart.streamlit.app/?event={urllib.parse.quote(clean_event)}"
            qr_img = qrcode.make(url)
            qr_img_array = np.array(qr_img.convert('RGB'))
            col1, col2 = st.columns([1, 1])
            with col1:
                st.image(qr_img_array, caption=f"📱 '{selected_event}' માટે QR કોડ", width=250)
                st.success(f"🔗 URL: {url}")
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
                st.write("1. આ QR કોડ પ્રિન્ટ કરીને ઇવેન્ટમાં મૂકો.")
                st.write("2. ગ્રાહકો સ્કેન કરશે એટલે સીધા ગ્રાહક પેજ પર જશે.")

# ============================================================
# PAGE 3: CLIENT SEARCH (ગ્રાહક માટેનું મુખ્ય પેજ)
# ============================================================
elif option == "🔍 ફોટો શોધો" or option == "🔍 ફોટો શોધો ટેસ્ટિંગ":
    if event_name_from_url:
        event_name = urllib.parse.unquote(str(event_name_from_url)).strip()
    else:
        st.markdown("### 🔍 તમારા ફોટા શોધો")
        available_events = list_all_local_events()
        if available_events:
            event_name = st.selectbox("📁 ઇવેન્ટ પસંદ કરો", available_events)
        else:
            st.info("ℹ️ હજુ સુધી કોઈ ઇવેન્ટ બની નથી.")
            st.stop()

    if event_name:
        if f"auth_{event_name}" not in st.session_state:
            st.session_state[f"auth_{event_name}"] = False
        
        # ગ્રાહક માટે ઇવેન્ટ પાસવર્ડ
        if not st.session_state[f"auth_{event_name}"]:
            st.markdown(f"### 🔒 '{event_name}' ઇવેન્ટ પાસવર્ડ")
            entered_password = st.text_input("🔑 પાસવર્ડ નાખો:", type="password")
            
            if st.button("🚪 પ્રવેશ કરો"):
                event_data = load_event_data_local(event_name)
                if event_data.get("password") == entered_password:
                    st.session_state[f"auth_{event_name}"] = True
                    st.success("✅ પાસવર્ડ સાચો છે!")
                    st.rerun()
                else:
                    st.error("❌ ખોટો પાસવર્ડ!")
            st.stop()

        st.markdown(f"### 🔍 '{event_name}' માં તમારા ફોટા શોધો")

        upload_option = st.radio(
            "ફોટો આપવાની રીત પસંદ કરો:",
            ["📸 કેમેરાથી સેલ્ફી લો", "📁 ગેલેરીમાંથી ફોટો પસંદ કરો"],
            index=0,
            key=f"upload_option_{event_name}"
        )

        uploaded_file = None
        if upload_option == "📸 કેમેરાથી સેલ્ફી લો":
            uploaded_file = st.camera_input("📸 અહીં સેલ્ફી લો", key=f"cam_{event_name}")
        else:
            uploaded_file = st.file_uploader("📁 ગેલેરીમાંથી ફોટો પસંદ કરો", type=["jpg", "jpeg", "png"], key=f"file_{event_name}")

        if uploaded_file is not None:
            event_data = load_event_data_local(event_name)
            all_db_faces = event_data.get("faces", [])
            
            if not all_db_faces:
                st.error("❌ આ ઇવેન્ટમાં હજુ સુધી કોઈ ફોટા સેવ થયેલા નથી.")
                st.stop()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_file:
                tmp_file.write(uploaded_file.getvalue())
                tmp_path = tmp_file.name

            img = cv2.imread(tmp_path)
            if img is not None:
                st.image(img, channels="BGR", caption="તમારો આપેલો ફોટો", width=250)
                with st.spinner("🔍 તમારા બધા ફોટા શોધાઈ રહ્યા છે... થોડીવાર રાહ જુઓ..."):
                    faces = app.get(img)
                    if len(faces) == 0:
                        st.warning("❌ ફોટામાં કોઈ ચહેરો ઓળખાયો નહીં! કૃપા કરીને ચોખ્ખો ફોટો આપો.")
                    else:
                        st.success("✅ ચહેરો ઓળખાઈ ગયો!")
                        user_face = faces[0]
                        user_emb = user_face.embedding / np.linalg.norm(user_face.embedding)

                        matched_photos = []
                        seen_files = set()

                        for item in all_db_faces:
                            db_emb = parse_embedding(item.get("embedding"))
                            if db_emb is not None:
                                sim = float(np.dot(user_emb, db_emb))
                                if sim >= 0.35:
                                    fname = item.get("filename")
                                    if fname not in seen_files:
                                        seen_files.add(fname)
                                        item_copy = dict(item)
                                        item_copy["similarity"] = sim
                                        matched_photos.append(item_copy)

                        matched_photos = sorted(matched_photos, key=lambda x: x["similarity"], reverse=True)

                        st.subheader("🖼️ તમારા મળેલા બધા ફોટા")
                        if matched_photos:
                            st.success(f"🎉 તમારા કુલ **{len(matched_photos)}** ફોટા મળ્યા છે!")
                            
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
                                            st.write(f"📁 {item.get('filename')}")

                                    price = PHOTO_PRICE
                                    cart_key = f"cart_{idx}_{item.get('filename')}"
                                    selected = st.checkbox(f"🛒 ₹{price}" if price > 0 else "🆓 FREE", key=cart_key)
                                    
                                    current_item = {
                                        "person": item.get("person_label", "MyPhoto"),
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

                            if st.button(f"➕ આ બધા ({len(matched_photos)}) ફોટા કાર્ટમાં ઉમેરો", key="add_all_matched"):
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
                            st.warning("⚠️ આ ઇવેન્ટમાંથી તમારો મેળ ખાતો કોઈ ફોટો મળ્યો નથી.")

                    # ============================================================
                    # 🛒 CART DISPLAY & SECURE RAZORPAY PAYMENT (સાઇડબાર)
                    # ============================================================
                    st.sidebar.markdown("---")
                    st.sidebar.markdown("## 🛒 તમારું કાર્ટ")
                    is_ready_to_download = False

                    if st.session_state.cart:
                        cart = st.session_state.cart
                        cart = st.session_state.get("cart", [])
                        total_price = sum(item.get("price", PHOTO_PRICE) for item in cart)

                        # કાર્ટની વસ્તુઓ ડિસ્પ્લે કરો
                        for idx, item in enumerate(cart):
                            price = item.get("price", PHOTO_PRICE)
                            if price == 0:
                                st.sidebar.write(f"{idx+1}. {item.get('person', 'Photo')} - 🆓 FREE")
                            else:
                                st.sidebar.write(f"{idx+1}. {item.get('person', 'Photo')} - ₹{price}")

                        st.sidebar.markdown(f"### 💰 કુલ રકમ: ₹{total_price}")

                        # કાર્ટ ખાલી કરવાનું બટન
                        if st.sidebar.button("🗑️ કાર્ટ ખાલી કરો", key="clear_cart_btn"):
                            st.session_state.cart = []
                            st.session_state.payment_done = False
                            st.session_state.show_checkout = False
                            st.session_state.payment_link_id = None
                            st.session_state.payment_url = None
                            st.rerun()

                        # Session State મેનેજમેન્ટ
                        if "payment_done" not in st.session_state:
                            st.session_state.payment_done = False
                        if "payment_link_id" not in st.session_state:
                            st.session_state.payment_link_id = None
                        if "payment_url" not in st.session_state:
                            st.session_state.payment_url = None

                        # ૧. પેમેન્ટ સેક્શન (જો રકમ > 0 હોય અને પેમેન્ટ બાકી હોય)
                        if total_price > 0 and not st.session_state.payment_done:
                            st.sidebar.markdown("---")
                            
                            # સ્ટેપ ૧: પેમેન્ટ લિંક બનાવવાનું બટન
                            if st.session_state.payment_link_id is None:
                                if st.sidebar.button(f"🧾 ચેકઆઉટ કરો (₹{total_price})", key="checkout_btn"):
                                    try:
                                        # કુલ રકમ પૈસામાં કન્વર્ટ કરો (₹1 = 100 paise)
                                        amount_in_paise = int(total_price * 100)
                                        
                                        link_data = {
                                            "amount": amount_in_paise,
                                            "currency": "INR",
                                            "description": f"{len(cart)} Photos Download",
                                        }
                                        res = razorpay_client.payment_link.create(link_data)
                                        st.session_state.payment_link_id = res["id"]
                                        st.session_state.payment_url = res["short_url"]
                                        st.rerun()
                                    except Exception as e:
                                        st.sidebar.error(f"પેમેન્ટ લિંક બનાવવામાં ભૂલ: {e}")

                            # સ્ટેપ ૨: ગ્રાહકને પેમેન્ટ લિંક અને Verify બટન આપો
                            if st.session_state.payment_link_id:
                               st.sidebar.link_button(
                                   label="💳 પેમેન્ટ કરો (Pay Now)",
                                   url=st.session_state.payment_url,
                                   type="primary",
                                   use_container_width=True
                                )
                            st.sidebar.caption("ઉપરના બટન પર ક્લિક કરીને પેમેન્ટ પૂર્ણ કરો.")
                            st.sidebar.caption("UPI / Card / NetBanking ઉપલબ્ધ છે.")

                            if st.sidebar.button("🔄 મેં પેમેન્ટ કરી દીધું (Verify)", key="verify_pay_btn"):
                                    try:
                                        status_res = razorpay_client.payment_link.fetch(st.session_state.payment_link_id)
                                        if status_res.get("status") == "paid":
                                            st.session_state.payment_done = True
                                            st.sidebar.success("✅ પેમેન્ટ સફળ થયું!")
                                            st.rerun()
                                        else:
                                            st.sidebar.warning("⚠️ પેમેન્ટ હજુ મળ્યું નથી. કૃપા કરીને પેમેન્ટ પૂર્ણ કરો.")
                                    except Exception as e:
                                        st.sidebar.error(f"વેરિફિકેશન એરર: {e}")

                        # ૨. ડાઉનલોડ સેક્શન (જ્યારે ફ્રી હોય અથવા પેમેન્ટ થઈ ગયું હોય)
                        is_ready_to_download = (total_price == 0) or st.session_state.payment_done

                        if is_ready_to_download:
                            st.sidebar.markdown("---")
                            st.sidebar.success("🎉 ફોટો ડાઉનલોડ માટે તૈયાર છે!")
                            
                            # અહીં તમારા ફોટાનો ZIP ડેટા હોવો જોઈએ (દા.ત. zip_data)
                            # જો zip_data મુખ્ય પેજ પર બનતું હોય તો ત્યાં પણ ડાઉનલોડ બટન રાખી શકાય
                            if "zip_data" in locals() or "zip_data" in globals():
                                st.sidebar.download_button(
                                    label="📥 બધા ફોટા ડાઉનલોડ કરો (ZIP)",
                                    file_name="event_photos.zip",
                                    mime="application/zip",
                                    key="sidebar_download_btn"
                                )
                            
                            # Telegram પર મેસેજ મોકલો
                            msg_sent = send_telegram_message(
                                f"💰 <b>નવું પેમેન્ટ મળ્યું!</b>\n"
                                f"📸 ઇવેન્ટ: {event_name}\n"        
                                f"🖼️ ફોટા સંખ્યા: {len(cart)}\n"
                                f"💵 રકમ: ₹{total_price}\n"
                                f"🕒 {datetime.datetime.now().strftime('%d-%m-%Y %H:%M')}"
                            )
                            if msg_sent:
                                st.sidebar.success("✅ પેમેન્ટ વિગત મોકલાઈ ગઈ!")
                            
                            # લાઇન 885 ની જગ્યાએ આ રીતે લખો:
                            if st.session_state.get("payment_done", False):
                                st.sidebar.markdown("---")
                                st.sidebar.success("🎉 ફોટો ડાઉનલોડ માટે તૈયાર છે!")
                                
                                # તમારું ડાઉનલોડ બટન
                                st.sidebar.download_button(
                                    label="📥 બધા ફોટા ડાઉનલોડ કરો (ZIP)",
                                    file_name="event_photos.zip",
                                    mime="application/zip",
                                    key="download_final_photos"
                                )
                        

                                # ૨. ડાઉનલોડ અને શેરિંગ
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
                                    
                                    for idx, item in enumerate(cart):
                                        file_id = item.get("drive_file_id")
                                        filename = item.get("filename", f"photo_{idx+1}.jpg")
                                        if file_id:
                                            d_link = f"https://drive.google.com/uc?export=download&id={file_id}"
                                            st.sidebar.markdown(f"📸 [{filename} ડાઉનલોડ કરો]({d_link})")

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
# FOOTER
# ============================================================
st.markdown("""
<div style="text-align: center; margin-top: 50px; color: #6c757d; font-size: 0.8rem;">
    📸 <strong>જય ફોટો શોધ</strong> - AI દ્વારા તમારા ફોટા શોધો<br>
    © 2026 Jay Photography | Made with ❤️ in Gujarat
</div>
""", unsafe_allow_html=True)