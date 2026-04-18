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
# 💡 提示：如果您貼在 B 檔案，可以把這裡改成 "創傷知情模擬器 (分流B)"
st.set_page_config(page_title="創傷知情模擬器 (分流A)", layout="wide") 

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

# --- API 輪替與防呆發送機制 (角色強化版) ---
def send_message_safely(text):
    """
    發送訊息，若失敗則自動切換至下一把 API Key 重試
    """
    time.sleep(1) # [防呆] 強制減速 1 秒
    
    # --- 關鍵防護：抽離 System Prompt 以鎖定角色 ---
    system_prompt = st.session_state.history[0]["content"]
    
    # 取得純對話歷史
    gemini_history = []
    for msg in st.session_state.history[1:]:
        g_role = "model" if msg["role"] == "assistant" else "user"
        gemini_history.append({"role": g_role, "parts": [msg["content"]]})
        
    api_keys = st.session_state.api_keys_list
    total_keys = len(api_keys)
    
    # 開始輪替嘗試
    for i in range(total_keys):
        current_key_index = (st.session_state.current_key_index + i) % total_keys
        active_key = api_keys[current_key_index]
        
        try:
            # 使用當前的 Key 初始化模型
            genai.configure(api_key=active_key)
            
            # 將學生的角色設定綁死在系統底層
            model = genai.GenerativeModel(
                model_name=st.session_state.valid_model_name,
                system_instruction=system_prompt, # <--- 防失憶關鍵！
                safety_settings={
                    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                }
            )
            
            # 使用目前的歷史紀錄建立 session
            chat_session = model.start_chat(history=gemini_history)
            response = chat_session.send_message(text)
            
            # 如果成功，記錄最後成功的 Key index，並回傳
            st.session_state.current_key_index = current_key_index
            return response.text
            
        except Exception as e:
            error_msg = str(e).lower()
            st.toast(f"⚠️ Key {current_key_index + 1} 發生狀況，嘗試切換...", icon="🔄")
            
            # 如果是最後一把 Key 也失敗了
            if i == total_keys - 1:
                if "429" in error_msg or "quota" in error_msg:
                    st.warning("🐌 哎呀！您輸入的速度太快，或是目前所有 API 額度都耗盡了。請稍等 1 分鐘後再試喔！")
                    return None
                else:
                    raise e
            # 如果不是最後一把，繼續迴圈嘗試下一把

# 初始化 Session State
if "history" not in st.session_state: st.session_state.history = []
if "loaded_text" not in st.session_state: st.session_state.loaded_text = ""
if "user_nickname" not in st.session_state: st.session_state.user_nickname = ""
if "current_persona" not in st.session_state: st.session_state.current_persona = {}
if "start_time" not in st.session_state: st.session_state.start_time = datetime.now()
if "chat_session_initialized" not in st.session_state: st.session_state.chat_session_initialized = False

# 多重 API Key 記憶機制
if "raw_api_key_input" not in st.session_state: st.session_state.raw_api_key_input = ""
if "api_keys_list" not in st.session_state: st.session_state.api_keys_list = []
if "current_key_index" not in st.session_state: st.session_state.current_key_index = 0
if "valid_model_name" not in st.session_state: st.session_state.valid_model_name = "gemini-2.5-flash" # 預設模型

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
        st.session_state.history = []
        st.session_state.current_persona = {}
        st.session_state.chat_session_initialized = False
        st.session_state.start_time = datetime.now() 
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.warning("🔑 請輸入您的 Gemini API Key (可輸入多組)")
st.sidebar.markdown("<small>提示：輸入多組 Key 請用半形逗號 `,` 隔開，可防當機</small>", unsafe_allow_html=True)

input_key = st.sidebar.text_input("在此貼上您的 API Key", type="password", value=st.session_state.raw_api_key_input)

if input_key:
    st.session_state.raw_api_key_input = input_key
    # 將逗號分隔的字串轉為 List，並清除空白
    st.session_state.api_keys_list = [k.strip() for k in input_key.split(",") if k.strip()]

if not st.session_state.api_keys_list:
    st.info("💡 提示：請先在側邊欄輸入至少一組 API Key，否則系統無法運作。")
    st.stop() 
    
# 模型偵測 (用第一把 Key 測試即可)
if st.session_state.api_keys_list:
    try:
        genai.configure(api_key=st.session_state.api_keys_list[0])
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if available_models:
            default_idx = available_models.index("models/gemini-2.5-flash") if "models/gemini-2.5-flash" in available_models else 0
            st.session_state.valid_model_name = st.sidebar.selectbox("🤖 AI 模型", available_models, index=default_idx)
    except: 
        st.sidebar.error("❌ 第一把 API Key 無效，請檢查。")

student_grade = st.sidebar.selectbox("學生年級 (新個案適用)", ["國小", "國中", "高中"])
lang = st.sidebar.selectbox("語言", ["繁體中文", "粵語", "English"])

# 顯示目前使用的 Key 狀態 (除錯或安心用)
st.sidebar.caption(f"🛡️ 目前備妥 {len(st.session_state.api_keys_list)} 把 API Key 輪替中")

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

if st.session_state.loaded_text and st.session_state.api_keys_list and st.session_state.valid_model_name:

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
                
                # 【加入括號表情指示的強化版 Prompt】
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
                5. Actions and Expressions: The user may use parentheses ( ) to describe their non-verbal behaviors. YOU MUST also use parentheses ( ) to describe the student's body language, facial expressions, or emotional state in your responses.
                """
                
                # 初始化對話歷史
                st.session_state.history = [{"role": "user", "content": sys_prompt}]
                st.session_state.chat_session_initialized = True
                
                # 使用輪替機制發送第一句
                resp_text = send_message_safely("Action: Start interaction based on context.")
                if resp_text:
                    st.session_state.history.append({"role": "assistant", "content": resp_text})
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
                        # 【續談時同樣加入括號表情指示】
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
                        Remember: YOU MUST use parentheses ( ) to describe the student's body language, facial expressions, or emotional state in your responses.
                        """
                        restored_history.append({"role":"user", "content": sys_prompt})
                        
                        # 略過原本的第一句 prompt，只載入對話內容
                        for index, row in df.iterrows():
                            if "Role: You are a" not in str(row['content']):
                                restored_history.append({"role": row['role'], "content": row['content']})
                        
                        st.session_state.history = restored_history
                        st.session_state.chat_session_initialized = True
                        
                        if st.button("🚀 繼續對話"):
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
            # 隱藏系統 Prompt，不讓使用者看到落落長的設定
            if "Role: You are a" not in msg["content"]:
                with st.chat_message(role):
                    st.write(msg["content"])

        if user_in := st.chat_input("老師回應... (可用括號描述動作，例如：(微笑點頭) 發生什麼事了？)"):
            st.session_state.history.append({"role": "user", "content": user_in})
            with st.chat_message("user"):
                st.write(user_in)
                
            with st.spinner("⏳ 學生正在思考如何回應 (為防超速，請稍候)..."):
                try:
                    # 使用自動輪替機制的安全發送函式
                    resp_text = send_message_safely(user_in)
                    
                    if resp_text: 
                        st.session_state.history.append({"role": "assistant", "content": resp_text})
                        auto_save_to_google_sheets(st.session_state.user_nickname, st.session_state.history)
                        st.rerun()
                except Exception as e:
                    st.error(f"❌ 發生嚴重錯誤: {e}")

# --- 7. 下載功能區 ---
st.sidebar.markdown("---")
if st.session_state.history:
    st.sidebar.subheader("💾 紀錄保存")
    df = pd.DataFrame(st.session_state.history)
    df['nickname'] = st.session_state.user_nickname
    df['time'] = (datetime.now() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    
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
