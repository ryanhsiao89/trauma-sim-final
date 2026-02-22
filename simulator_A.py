import streamlit as st
import os
import random
import glob
import pandas as pd
import json
from datetime import datetime, timedelta
from pypdf import PdfReader
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time

# --- 1. 系統設定 ---
st.set_page_config(page_title="創傷知情模擬器 (研究完全版)", layout="wide")

# --- Google Sheets 背景自動上傳函式 (Auto-Save 版) ---
def auto_save_to_google_sheets(user_id, chat_history):
    """每次對話更新時，自動在背景覆寫/更新該次對話紀錄"""
    if not chat_history:
        return False
        
    try:
        # 1. 連線與設定
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = dict(st.secrets["gcp_service_account"])
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        # 2. 開啟試算表
        sheet = client.open("2025創傷知情研習數據") 
        worksheet = sheet.worksheet("Simulator")
        
        # 3. 準備資料
        tw_fix = timedelta(hours=8)
        start_t = st.session_state.get('start_time', datetime.now())
        login_str = (start_t + tw_fix).strftime("%Y-%m-%d %H:%M:%S")
        end_t = datetime.now()
        logout_str = (end_t + tw_fix).strftime("%Y-%m-%d %H:%M:%S") # 視為最後更新時間
        duration_mins = round((end_t - start_t).total_seconds() / 60, 2)
        
        # 建立專屬的 Session ID (用登入時間標記這回合對話)
        session_id = f"{user_id}_{login_str}"
        
        # 4. 整理對話內容
        scenario = st.session_state.get("current_persona", {})
        basic_info = f"角色:{scenario.get('name','未知')}/觸發:{scenario.get('trigger','未知')}"
        adv_info = f"第{scenario.get('session_num',1)}次/關係:{scenario.get('relation','未知')}/前情:{scenario.get('recent_event','無')}"
        scenario_str = f"{basic_info} | {adv_info}"
        
        full_conversation = f"【演練案例】：{scenario_str}\n\n"
        for msg in chat_history:
            role = msg.get("role", "Unknown")
            content = ""
            if "parts" in msg:
                content = msg["parts"][0] if isinstance(msg["parts"], list) else str(msg["parts"])
            elif "content" in msg:
                content = msg["content"]
            full_conversation += f"[{role}]: {content}\n"

        # 5. 尋找並更新，或新增一筆
        records = worksheet.get_all_records()
        row_to_update = None
        col_logins = worksheet.col_values(1) # 第一欄：登入時間
        col_ids = worksheet.col_values(3)    # 第三欄：學員編號
        
        for i in range(1, len(col_logins)): # 跳過標題列
            if i < len(col_ids) and col_logins[i] == login_str and str(col_ids[i]) == str(user_id):
                row_to_update = i + 1 # Gspread 索引從 1 開始
                break
                
        # 計算累積次數
        login_count = col_ids.count(str(user_id))
        if row_to_update is None:
            login_count += 1 # 新增一筆
            
        data_row = [login_str, logout_str, user_id, duration_mins, login_count, full_conversation]
        
        if row_to_update:
            # 更新既有列 (A:F)
            cell_range = f'A{row_to_update}:F{row_to_update}'
            worksheet.update(cell_range, [data_row])
        else:
            # 新增一列
            worksheet.append_row(data_row)
            
        return True
    except Exception as e:
        print(f"背景上傳失敗: {e}") # 背景報錯不干擾使用者
        return False

# --- 防呆防超速發送函式 ---
def send_message_safely(chat_session, text):
    """帶有強制延遲與錯誤處理的發送機制"""
    # [防呆 1] 強制減速：每次發話前強制等 2 秒，避免老師按太快
    time.sleep(2) 
    
    try:
        response = chat_session.send_message(text)
        return response.text
    except Exception as e:
        error_msg = str(e).lower()
        if "429" in error_msg or "quota" in error_msg:
            # [防呆 2] 友善的超速提醒
            st.warning("🐌 哎呀！您輸入的速度太快了，AI 老師喘不過氣來。請稍等 10 秒鐘後再試一次喔！(免費版速度限制)")
            return None
        else:
            raise e # 其他嚴重錯誤照常拋出

# 初始化 Session State
if "history" not in st.session_state: st.session_state.history = []
if "loaded_text" not in st.session_state: st.session_state.loaded_text = ""
if "user_nickname" not in st.session_state: st.session_state.user_nickname = ""
if "current_persona" not in st.session_state: st.session_state.current_persona = {}
if "start_time" not in st.session_state: st.session_state.start_time = datetime.now()
if "chat_session_initialized" not in st.session_state: st.session_state.chat_session_initialized = False
# 【新增】確保 API Key 被安全記憶
if "api_key" not in st.session_state: st.session_state.api_key = ""

# --- 2. 登入區 ---
if not st.session_state.user_nickname:
    st.title("🛡️ 歡迎來到創傷知情模擬器")
    st.info("請輸入您的研究編號 (ID) 以開始練習。")
    nickname_input = st.text_input("請輸入您的編號：", placeholder="例如：001, 002...") 
    
    if st.button("🚀 進入系統"):
        if nickname_input.strip():
            st.session_state.user_nickname = nickname_input
            st.session_state.start_time = datetime.now()
            st.rerun()
        else:
            st.error("❌ 編號不能為空！")
    st.stop()

# --- 3. 側邊欄設定 ---
st.sidebar.title(f"👤 學員: {st.session_state.user_nickname}")
st.sidebar.markdown("*(系統已開啟自動存檔功能)*")
st.sidebar.markdown("---")

# 返回首頁按鈕
if st.session_state.chat_session_initialized:
    st.sidebar.markdown("### 🏠 導覽")
    if st.sidebar.button("返回首頁 / 換個個案", type="secondary"):
        # 清除當前對話狀態，但不登出，且【保留 API Key】
        st.session_state.history = []
        st.session_state.current_persona = {}
        st.session_state.chat_session_initialized = False
        st.session_state.start_time = datetime.now() # 重置時間以開啟新的 Session
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.warning("🔑 請輸入您自己的 Gemini API Key 以開始演練")

# 【改良】利用 value 綁定 session_state，讓系統記住 API Key
input_key = st.sidebar.text_input("在此貼上您的 API Key", type="password", value=st.session_state.api_key)

# 一旦使用者輸入，就立刻存入深層記憶中
if input_key:
    st.session_state.api_key = input_key

# 檢查記憶體中是否有 API Key
if not st.session_state.api_key:
    st.info("💡 提示：請先在側邊欄輸入 API Key，否則系統無法運作。")
    st.stop() 
    
valid_model_name = None
if st.session_state.api_key:
    try:
        genai.configure(api_key=st.session_state.api_key)
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if available_models:
            valid_model_name = st.sidebar.selectbox("🤖 AI 模型", available_models)
    except: 
        st.sidebar.error("❌ API Key 無效")

student_grade = st.sidebar.selectbox("學生年級 (新個案適用)", ["國小", "國中", "高中"])
lang = st.sidebar.selectbox("語言", ["繁體中文", "粵語", "English"])

# --- 4. 自動讀取教材 ---
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
        st.warning("⚠️ 倉庫中找不到 PDF 檔案。")

# --- 5. 隨機劇本生成器 (基礎資料) ---
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

if st.session_state.loaded_text and st.session_state.api_key and valid_model_name:
    model = genai.GenerativeModel(
        model_name=valid_model_name,
        safety_settings={
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        }
    )

    if not st.session_state.chat_session_initialized:
        tab1, tab2 = st.tabs(["🎲 隨機生成新個案", "📂 載入舊紀錄續談"])
        
        # [模式一] 隨機新個案 
        with tab1:
            st.markdown("### 設定演練情境")
            with st.expander("⚙️ 進階設定：自訂晤談情境 (非必填)", expanded=False):
                col1, col2 = st.columns(2)
                with col1:
                    session_num = st.slider("這是第幾次晤談？", 1, 10, 1)
                with col2:
                    rel_status = st.selectbox("目前的信任關係", ["初次見面 / 不熟", "建立信任中", "關係良好 / 依賴", "關係破裂 / 敵對", "冷淡 / 防衛"], index=0)
                recent_event = st.text_input("近期發生事件 / 前情提要", value="無特殊事件，日常互動。")

            if st.button("🎲 生成案例並開始", type="primary"):
                persona = generate_random_persona(student_grade)
                persona['session_num'] = session_num
                persona['relation'] = rel_status
                persona['recent_event'] = recent_event
                st.session_state.current_persona = persona
                
                sys_prompt = f"""
                Role: You are a {persona['grade']} student named {persona['name']}. 
                
                [CORE PROFILE]
                Trauma Background: {persona['background']}. 
                Current Trigger: {persona['trigger']}.
                Dominant Response Mode: {persona['response_mode']}.
                
                [SCENARIO CONTEXT]
                - Session Number: This is the {session_num} time you are talking to this teacher.
                - Relationship Quality: {rel_status}.
                - Recent Life Event: {recent_event}.
                
                [KNOWLEDGE BASE]
                {st.session_state.loaded_text[:25000]}
                
                [INSTRUCTIONS]
                1. Act strictly according to the 'Scenario Context'. 
                   - If session > 1, do NOT introduce yourself like a stranger.
                   - If relationship is bad, be guarded or hostile.
                   - If relationship is good, show some trust but still react to the trigger.
                2. Respond naturally based on your response mode ({persona['response_mode']}).
                3. Language: {lang}.
                4. Stay in character. Do not explain you are an AI.
                """
                
                st.session_state.chat_session = model.start_chat(history=[{"role":"user","parts":[sys_prompt]},{"role":"model","parts":["Ready."]}])
                
                start_action = "Action: Start interaction based on context."
                # 這裡不需要延遲，因為是系統初始化發送
                resp = st.session_state.chat_session.send_message(start_action)
                st.session_state.history.append({"role": "assistant", "content": resp.text})
                st.session_state.chat_session_initialized = True
                # 初始化後儲存第一筆紀錄
                auto_save_to_google_sheets(st.session_state.user_nickname, st.session_state.history)
                st.rerun()
        
        # [模式二] 載入舊檔
        with tab2:
            st.markdown("### 延續之前的演練")
            uploaded_file = st.file_uploader("請上傳上次下載的 .csv 紀錄檔", type=['csv'])
            
            if uploaded_file is not None:
                try:
                    df = pd.read_csv(uploaded_file)
                    if 'meta_persona' in df.columns:
                        persona_json = df['meta_persona'].iloc[0]
                        st.session_state.current_persona = json.loads(persona_json)
                        p = st.session_state.current_persona
                        st.success(f"✅ 成功載入個案：{p['name']} (第{p.get('session_num','?')}次晤談)")
                        
                        restored_history = []
                        gemini_history = []
                        
                        sys_prompt = f"""
                        Role: You are a {p['grade']} student named {p['name']}. 
                        Trauma Background: {p['background']}. 
                        Trigger: {p['trigger']}.
                        Response Mode: {p['response_mode']}.
                        
                        [CONTEXT RESUMED]
                        - Session Num: {p.get('session_num', 1)}
                        - Relationship: {p.get('relation', 'Unknown')}
                        - Recent Event: {p.get('recent_event', 'Unknown')}
                        
                        Knowledge Base: {st.session_state.loaded_text[:25000]}
                        
                        Instruction: Continue the conversation naturally. Language: {lang}.
                        """
                        gemini_history.append({"role":"user","parts":[sys_prompt]})
                        gemini_history.append({"role":"model","parts":["Ready."]})
                        
                        for index, row in df.iterrows():
                            role = row['role']
                            content = row['content']
                            restored_history.append({"role": role, "content": content})
                            g_role = "model" if role == "assistant" else "user"
                            gemini_history.append({"role": g_role, "parts": [str(content)]})
                        
                        st.session_state.history = restored_history
                        st.session_state.chat_session = model.start_chat(history=gemini_history)
                        st.session_state.chat_session_initialized = True
                        
                        if st.button("🚀 繼續對話"):
                            # 重設 start_time 以開展新的 Session ID
                            st.session_state.start_time = datetime.now()
                            st.rerun()
                    else:
                        st.error("❌ 這個 CSV 檔案不包含個案設定資料，無法用於續談。")
                except Exception as e:
                    st.error(f"❌ 檔案讀取失敗: {e}")

    # C. 顯示對話
    if st.session_state.chat_session_initialized:
        p = st.session_state.current_persona
        st.info(f"🎭 **演練中**：{p.get('grade')}生 **{p.get('name')}** | 第 {p.get('session_num',1)} 次晤談 | 關係：{p.get('relation','未知')} | 前情：{p.get('recent_event','無')}")
        
        for msg in st.session_state.history:
            role = "assistant" if msg["role"] == "assistant" else "user"
            with st.chat_message(role):
                st.write(msg["content"])

        if user_in := st.chat_input("老師回應..."):
            st.session_state.history.append({"role": "user", "content": user_in})
            with st.chat_message("user"):
                st.write(user_in)
                
            with st.spinner("⏳ 學生正在思考如何回應 (為防超速，請稍候)..."):
                try:
                    # 使用安全發送函式 (內建延遲與防呆)
                    resp_text = send_message_safely(st.session_state.chat_session, user_in)
                    
                    if resp_text: # 如果沒被限速擋下
                        st.session_state.history.append({"role": "assistant", "content": resp_text})
                        # 【背景自動存檔】
                        auto_save_to_google_sheets(st.session_state.user_nickname, st.session_state.history)
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
    
    persona_json = json.dumps(st.session_state.current_persona, ensure_ascii=False)
    df['meta_persona'] = persona_json
    
    csv = df.to_csv(index=False).encode('utf-8-sig')
    
    st.sidebar.download_button(
        label="📥 下載對話紀錄 (含續談資料)",
        data=csv,
        file_name=f"模擬器_{st.session_state.user_nickname}_{st.session_state.current_persona.get('name')}.csv",
        mime="text/csv",
        help="下載此檔案可保留目前的對話進度與情境設定。"
    )
