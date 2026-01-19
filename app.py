import streamlit as st
import os
import random
import glob
import pandas as pd
from datetime import datetime
from pypdf import PdfReader
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# --- 1. 系統設定 ---
st.set_page_config(page_title="創傷知情模擬器 (全文本升級版)", layout="wide")
# --- 0. 檢查是否剛登出 ---
if st.session_state.get("logout_triggered"):
    st.markdown("## ✅ 已成功登出")
    st.success("您的對話紀錄已安全上傳至雲端。感謝您的參與！")
    st.write("如果您需要再次練習，請點擊下方按鈕。")
    
    if st.button("🔄 重新登入"):
        # 清除登出標記，讓系統回到初始狀態
        st.session_state.logout_triggered = False
        st.rerun()
    
    # 這一行 st.stop() 必須跟上面的 st.markdown 對齊 (縮排 4 格)
    st.stop()
# --- Google Sheets 上傳函式 (研究旗艦版) ---
def save_to_google_sheets(user_id, chat_history):
    try:
        # 1. 連線與設定
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = st.secrets["gcp_service_account"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        sheet = client.open("2025創傷知情研習數據")
        worksheet = sheet.worksheet("Simulator")
        
        # 2. 時間計算 (全部校正為台灣時間 UTC+8)
        tw_fix = timedelta(hours=8)
        
        # A. 取得登入時間 (如果沒抓到，就用現在)
        start_t = st.session_state.get('start_time', datetime.now())
        login_str = (start_t + tw_fix).strftime("%Y-%m-%d %H:%M:%S")
        
        # B. 取得登出時間 (就是現在)
        end_t = datetime.now()
        logout_str = (end_t + tw_fix).strftime("%Y-%m-%d %H:%M:%S")
        
        # C. 計算使用分鐘數 (Python 直接算，精準到小數點下2位)
        duration_mins = round((end_t - start_t).total_seconds() / 60, 2)
        
        # D. 計算累積次數 (讀取 C 欄「學員編號」來計算)
        # 注意：如果您的編號不在第3欄(Col C)，這裡的 col_values(3) 要改
        try:
            all_ids = worksheet.col_values(3) 
            # 計算這個 user_id 出現過幾次，然後 +1 (這次)
            login_count = all_ids.count(user_id) + 1
        except:
            login_count = 1 # 如果讀取失敗，當作第1次

        # 3. 整理對話內容
        scenario = st.session_state.get("student_persona", "未記錄")
        full_conversation = f"【演練案例】：{scenario}\n\n"
        for msg in chat_history:
            role = msg.get("role", "Unknown")
            content = ""
            if "parts" in msg:
                content = msg["parts"][0] if isinstance(msg["parts"], list) else str(msg["parts"])
            elif "content" in msg:
                content = msg["content"]
            full_conversation += f"[{role}]: {content}\n"

        # 4. 寫入六大欄位：[登入, 登出, 編號, 分鐘數, 次數, 內容]
        worksheet.append_row([
            login_str, 
            logout_str, 
            user_id, 
            duration_mins, 
            login_count, 
            full_conversation
        ])
        return True
    except Exception as e:
        st.error(f"上傳失敗: {e}")
        return False

# 初始化 Session State
if "history" not in st.session_state: st.session_state.history = []
if "loaded_text" not in st.session_state: st.session_state.loaded_text = ""
if "user_nickname" not in st.session_state: st.session_state.user_nickname = ""
if "current_persona" not in st.session_state: st.session_state.current_persona = {}

# --- 2. 登入區 ---
if not st.session_state.user_nickname:
    st.title("🛡️ 歡迎來到創傷知情模擬器")
    st.info("請輸入您的研究編號 (ID) 以開始練習。") 
    # 下面這行改了提示文字，但變數名稱維持不變，確保系統穩定
    nickname_input = st.text_input("請輸入您的編號：", placeholder="例如：001, 002...") 
    
if st.button("🚀 進入系統"):
        if nickname_input.strip():
            st.session_state.user_nickname = nickname_input
            # --- 新增：按下登入時，記錄現在時間 ---
            st.session_state.start_time = datetime.now()
            st.rerun()
        else:
            st.error("❌ 編號不能為空！")

    # 這一行已經幫您對齊好了 (縮排 4 格空白)
st.stop()

# --- 3. 側邊欄設定 ---
st.sidebar.title(f"👤 學員: {st.session_state.user_nickname}")
st.sidebar.markdown("---")
st.sidebar.markdown("### 📤 結束練習")
if st.sidebar.button("上傳紀錄並登出"):
    # 確保有對話才上傳
    if not st.session_state.history:
        st.sidebar.warning("還沒有對話紀錄喔！")
    else:
        with st.spinner("正在上傳數據至雲端..."):
            # 這裡會抓取您剛剛設定的 user_nickname (也就是編號)
# 執行上傳
            if save_to_google_sheets(st.session_state.user_nickname, st.session_state.history):
                st.sidebar.success("✅ 上傳成功！")
                time.sleep(1) # 稍微停頓一下
                
                # 1. 先清空所有狀態 (包含編號、對話紀錄)
                st.session_state.clear()
                
                # 2. 留下一個「已登出」的記號 (這是關鍵！)
                st.session_state.logout_triggered = True
                
                # 3. 重新整理 (這時程式會重跑，並被步驟1攔截，顯示登出畫面)
                st.rerun()

# 強制顯示輸入框，解決資源耗盡問題
st.sidebar.warning("🔑 請輸入您自己的 Gemini API Key 以開始演練")
api_key = st.sidebar.text_input("在此貼上您的 API Key", type="password")

if not api_key:
    st.info("💡 提示：請先在側邊欄輸入 API Key，否則系統無法運作。")
    st.stop() 
    
# 自動偵測模型
valid_model_name = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if available_models:
            valid_model_name = st.sidebar.selectbox("🤖 AI 模型", available_models)
    except: 
        st.sidebar.error("❌ API Key 無效")

student_grade = st.sidebar.selectbox("學生年級", ["國小", "國中", "高中"])
lang = st.sidebar.selectbox("語言", ["繁體中文", "粵語", "English"])

# --- 4. 自動讀取教材 (升級：讀取倉庫內所有 PDF) ---
if not st.session_state.loaded_text:
    combined_text = ""
    pdf_files = glob.glob("*.pdf")
    
    if pdf_files:
        with st.spinner(f"📚 系統正在內化 {len(pdf_files)} 份教材..."):
            try:
                for filename in pdf_files:
                    reader = PdfReader(filename)
                    for page in reader.pages:
                        text = page.extract_text()
                        if text: combined_text += text + "\n"
                st.session_state.loaded_text = combined_text
            except Exception as e:
                st.error(f"❌ 教材讀取失敗: {e}")
    else:
        st.warning("⚠️ 倉庫中找不到 PDF 檔案，請確認已上傳教材。")

# --- 5. 隨機劇本生成器 ---
def generate_random_persona(grade):
    names = ["小明", "小華", "安安", "凱凱", "婷婷", "阿宏"]
    backgrounds = ["長期被忽視", "目睹家暴", "照顧者情緒不穩", "曾受肢體暴力"]
    triggers = ["被當眾糾正", "感覺不公平", "環境吵雜", "被誤會"]
    responses = ["戰 (Fight) - 頂嘴/憤怒", "逃 (Flight) - 逃避", "凍結 (Freeze) - 呆滯", "討好 (Fawn) - 過度道歉"]
    return {
        "name": random.choice(names),
        "background": random.choice(backgrounds),
        "trigger": random.choice(triggers),
        "response_mode": random.choice(responses),
        "grade": grade
    }

# --- 6. 模擬器主畫面 ---
st.title("🛡️ 創傷知情模擬器")

if st.session_state.loaded_text and api_key and valid_model_name:
    model = genai.GenerativeModel(
        model_name=valid_model_name,
        safety_settings={
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
    )

    # A. 開始按鈕
    if len(st.session_state.history) == 0:
        if st.button("🎲 隨機生成案例並開始演練", type="primary"):
            persona = generate_random_persona(student_grade)
            st.session_state.current_persona = persona
            
            sys_prompt = f"""
            Role: You are a {persona['grade']} student named {persona['name']}. 
            Your trauma background: {persona['background']}. 
            Your current trigger: {persona['trigger']}.
            Your response mode: {persona['response_mode']}.
            
            Professional Knowledge Base: {st.session_state.loaded_text[:25000]}
            
            Instruction: 
            1. Respond naturally based on your response mode ({persona['response_mode']}).
            2. Language: {lang}.
            3. Stay in character. Do not explain you are an AI.
            """
            st.session_state.chat_session = model.start_chat(history=[{"role":"user","parts":[sys_prompt]},{"role":"model","parts":["Ready."]}])
            resp = st.session_state.chat_session.send_message("Action: Start.")
            st.session_state.history.append({"role": "assistant", "content": resp.text})
            st.rerun()

    # B. 顯示對話紀錄
    for msg in st.session_state.history:
        role = "assistant" if msg["role"] == "assistant" else "user"
        with st.chat_message(role):
            st.write(msg["content"])

    if user_in := st.chat_input("老師回應..."):
        st.session_state.history.append({"role": "user", "content": user_in})
        try:
            resp = st.session_state.chat_session.send_message(user_in)
            st.session_state.history.append({"role": "assistant", "content": resp.text})
            st.rerun()
        except Exception as e:
            st.error(f"❌ 發生錯誤: {e}")

# --- 7. 下載功能區 ---
st.sidebar.markdown("---")
if st.session_state.history:
    st.sidebar.subheader("💾 紀錄保存")
    df = pd.DataFrame(st.session_state.history)
    df['nickname'] = st.session_state.user_nickname
    df['time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    csv = df.to_csv(index=False).encode('utf-8-sig')
    
    st.sidebar.download_button(
        label="📥 下載對話紀錄 (CSV)",
        data=csv,
        file_name=f"模擬器紀錄_{st.session_state.user_nickname}.csv",
        mime="text/csv"
    )
