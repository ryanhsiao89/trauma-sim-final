import streamlit as st
import google.generativeai as genai
import random
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. 系統設定 ---
st.set_page_config(page_title="創傷知情模擬器 (研究版)", layout="wide")

# 設定通行碼
ACCESS_CODE = "TIC2025" 

if "auth" not in st.session_state: st.session_state.auth = False
if "history" not in st.session_state: st.session_state.history = []
if "user_nickname" not in st.session_state: st.session_state.user_nickname = ""
if "start_time" not in st.session_state: st.session_state.start_time = datetime.now()
# 模擬器專用：保存學生設定，避免重新整理後消失
if "student_persona" not in st.session_state: st.session_state.student_persona = ""

# --- 2. Google Sheets 上傳函式 ---
def save_to_google_sheets(nickname, chat_history):
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = st.secrets["gcp_service_account"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        sheet = client.open("2025創傷知情研習數據") # 您的試算表名稱
        worksheet = sheet.worksheet("Simulator")     # 指定寫入 Simulator 分頁
        
        end_time = datetime.now()
        duration = round((end_time - st.session_state.start_time).total_seconds() / 60, 2)
        turn_count = len([m for m in chat_history if m["role"] == "user"])
        
        full_conversation = f"【學生設定】: {st.session_state.student_persona}\n\n"
        for msg in chat_history:
            role = "AI" if msg["role"] == "model" else "User"
            full_conversation += f"[{role}]: {msg['parts'][0]}\n"

        worksheet.append_row([
            end_time.strftime("%Y-%m-%d %H:%M:%S"),
            nickname,
            duration,
            turn_count,
            full_conversation
        ])
        return True
    except Exception as e:
        st.error(f"上傳失敗: {str(e)}")
        return False

# --- 3. 登入畫面 ---
if not st.session_state.auth:
    st.title("🛡️ 創傷知情模擬演練")
    st.info("請輸入通行碼以開始演練。")
    col1, col2 = st.columns(2)
    with col1:
        pass_input = st.text_input("通行碼", type="password")
    with col2:
        nick_input = st.text_input("您的暱稱")
        
    if st.button("登入系統"):
        if pass_input == ACCESS_CODE and nick_input.strip():
            st.session_state.auth = True
            st.session_state.user_nickname = nick_input
            st.rerun()
        else:
            st.error("❌ 通行碼錯誤")
    st.stop()

# --- 4. 主程式 ---
st.sidebar.title(f"👤 {st.session_state.user_nickname}")
st.sidebar.markdown("---")

# 上傳按鈕
st.sidebar.markdown("### ☁️ 結束演練")
if st.sidebar.button("📤 上傳紀錄並登出"):
    if not st.session_state.history:
        st.sidebar.warning("請先進行對話再上傳")
    else:
        with st.spinner("正在上傳數據..."):
            if save_to_google_sheets(st.session_state.user_nickname, st.session_state.history):
                st.sidebar.success("✅ 上傳成功！")
                st.session_state.history = []
                st.session_state.student_persona = ""
                st.session_state.auth = False
                st.rerun()

# 模擬器邏輯
st.title("🛡️ 創傷知情模擬器")

# 如果還沒有產生過學生，或是歷史紀錄為空，就產生一個新學生
if not st.session_state.student_persona:
    scenarios = [
        "小強，14歲，上課常趴睡，叫醒會暴怒。背景：長期目睹家暴，睡眠不足。",
        "小美，10歲，過度焦慮，作業沒寫完會哭泣發抖。背景：主要照顧者要求極高，有以愛為名的情緒勒索。",
        "阿偉，16歲，冷漠抗拒，對老師的關心說『不用你管』。背景：多次被信任的大人背叛，習得無助。"
    ]
    st.session_state.student_persona = random.choice(scenarios)
    # 初始化對話
    st.session_state.history = [
        {"role": "user", "parts": [f"你現在扮演一位有創傷背景的學生：{st.session_state.student_persona}。請用第一人稱與我對話，剛開始你會表現出防衛或退縮，直到我運用創傷知情技巧建立連結。請不要一次講太多話，反應要像真實學生。"]},
        {"role": "model", "parts": ["好的，我現在是這個學生。老師，你找我幹嘛？我只是趴著休息一下而已..."]}
    ]

# 顯示學生背景
st.info(f"🎭 當前演練對象：{st.session_state.student_persona}")

# 顯示對話
for msg in st.session_state.history:
    if msg["role"] == "user" and "你現在扮演" in msg["parts"][0]: continue # 隱藏系統提示
    role = "assistant" if msg["role"] == "model" else "user"
    with st.chat_message(role):
        st.write(msg["parts"][0])

# 輸入框
if prompt := st.chat_input("老師請回應..."):
    # 讀取 Secret 裡的 Key
    if "GEMINI_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    else:
        st.error("找不到 API Key")
        st.stop()
        
    st.session_state.history.append({"role": "user", "parts": [prompt]})
    with st.chat_message("user"):
        st.write(prompt)
        
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        chat = model.start_chat(history=st.session_state.history)
        response = chat.send_message(prompt)
        
        st.session_state.history.append({"role": "model", "parts": [response.text]})
        with st.chat_message("assistant"):
            st.write(response.text)
    except Exception as e:
        st.error(f"AI 回應錯誤: {e}")
