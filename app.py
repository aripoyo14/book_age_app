import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import altair as alt
import re
from kanjize import kanji2number
import google.generativeai as genai
from dotenv import load_dotenv

# .envファイルを読み込む
load_dotenv()

# --- 設定 ---
SPREADSHEET_NAME = "bookdata" # スプシの名前（データ収集スクリプトと一致させる）
JSON_FILE = "service_account.json" # ローカル用の鍵ファイル名

st.set_page_config(page_title="年齢別・書籍マップ", layout="wide")

st.title('📚 "〇〇歳からの" 書籍年齢分布マップ')
st.markdown("国立国会図書館のデータから、対象年齢が明記された書籍を可視化します。")

# --- 年齢抽出関数 ---
def is_kara_pattern(title):
    """「〇〇歳からの」というパターンが含まれているかチェック"""
    if not title or pd.isna(title):
        return False
    try:
        title_str = str(title)
        # パターン1: 数字 + 歳からの (例: 13歳からの、１３歳からの)
        if re.search(r'\d+歳からの', title_str):
            return True
        # パターン2: 漢数字 + 歳からの (例: 十三歳からの)
        if re.search(r'[一二三四五六七八九十百]+歳からの', title_str):
            return True
    except Exception:
        pass
    return False

def extract_age(title):
    """タイトルから年齢を抽出する関数（漢数字対応版、「〇〇歳からの」パターン対応）"""
    if not title or pd.isna(title):
        return None
    try:
        title_str = str(title)
        # パターン1: 数字 + 歳からの (例: 13歳からの)
        match = re.search(r'(\d+)歳からの', title_str)
        if match:
            return int(match.group(1))
        
        # パターン2: 漢数字 + 歳からの (例: 十三歳からの)
        match_kanji = re.search(r'([一二三四五六七八九十百]+)歳からの', title_str)
        if match_kanji:
            return kanji2number(match_kanji.group(1))
    except Exception:
        pass
    return None

def extract_decade(publish_date):
    """発行日から年代を抽出する関数（例: 1995 → 1990年代）"""
    if not publish_date or pd.isna(publish_date):
        return None
    try:
        date_str = str(publish_date)
        # 4桁の年を抽出（例: "1995" や "1995-01-01" から "1995" を抽出）
        year_match = re.search(r'(\d{4})', date_str)
        if year_match:
            year = int(year_match.group(1))
            # 年代を計算（例: 1995 → 1990年代）
            decade = (year // 10) * 10
            return f"{decade}年代"
    except Exception:
        pass
    return None

# --- 統計データ集計関数 ---
def aggregate_statistics(df_with_age):
    """年齢別書籍数の統計データを集計する関数"""
    stats = {}
    
    # 基本統計
    valid_ages = df_with_age["対象年齢"].dropna()
    stats["総書籍数"] = len(df_with_age)
    stats["平均対象年齢"] = float(valid_ages.mean()) if len(valid_ages) > 0 else 0
    stats["最小年齢"] = int(valid_ages.min()) if len(valid_ages) > 0 else 0
    stats["最大年齢"] = int(valid_ages.max()) if len(valid_ages) > 0 else 0
    stats["中央値年齢"] = float(valid_ages.median()) if len(valid_ages) > 0 else 0
    
    # 年齢別の書籍数（上位10位）
    age_counts = df_with_age["対象年齢"].value_counts().sort_values(ascending=False)
    stats["年齢別書籍数（上位10位）"] = {
        int(age): int(count) for age, count in age_counts.head(10).items()
    }
    
    # ピーク年齢（書籍数が最多の年齢）
    if len(age_counts) > 0:
        stats["ピーク年齢"] = int(age_counts.index[0])
        stats["ピーク年齢の書籍数"] = int(age_counts.iloc[0])
    
    # 年代別の書籍数（もしあれば）
    if "年代" in df_with_age.columns:
        decade_counts = df_with_age["年代"].dropna().value_counts()
        stats["年代別書籍数"] = {
            decade: int(count) for decade, count in decade_counts.items()
        }
    
    # 年齢帯別の書籍数（10歳区切り）
    age_groups = {
        "0-9歳": len(df_with_age[(df_with_age["対象年齢"] >= 0) & (df_with_age["対象年齢"] < 10)]),
        "10-19歳": len(df_with_age[(df_with_age["対象年齢"] >= 10) & (df_with_age["対象年齢"] < 20)]),
        "20-29歳": len(df_with_age[(df_with_age["対象年齢"] >= 20) & (df_with_age["対象年齢"] < 30)]),
        "30-39歳": len(df_with_age[(df_with_age["対象年齢"] >= 30) & (df_with_age["対象年齢"] < 40)]),
        "40-49歳": len(df_with_age[(df_with_age["対象年齢"] >= 40) & (df_with_age["対象年齢"] < 50)]),
        "50-59歳": len(df_with_age[(df_with_age["対象年齢"] >= 50) & (df_with_age["対象年齢"] < 60)]),
        "60-69歳": len(df_with_age[(df_with_age["対象年齢"] >= 60) & (df_with_age["対象年齢"] < 70)]),
        "70-79歳": len(df_with_age[(df_with_age["対象年齢"] >= 70) & (df_with_age["対象年齢"] < 80)]),
        "80-89歳": len(df_with_age[(df_with_age["対象年齢"] >= 80) & (df_with_age["対象年齢"] < 90)]),
        "90歳以上": len(df_with_age[df_with_age["対象年齢"] >= 90]),
    }
    stats["年齢帯別書籍数"] = age_groups
    
    return stats

# --- Gemini APIで考察記事を生成する関数 ---
def generate_article_with_gemini(stats, writing_style="標準的", user_insights=""):
    """Gemini APIを使って年齢別書籍数の考察記事を生成"""
    try:
        # APIキーの取得（secrets.tomlから取得）
        api_key = None
        
        # パターン1: [gemini]セクション内のapi_keyキー（gcp_service_accountと同じパターン）
        if "gemini" in st.secrets:
            gemini_section = st.secrets["gemini"]
            if isinstance(gemini_section, dict) and "api_key" in gemini_section:
                api_key = gemini_section["api_key"]
        
        # パターン2: 後方互換性のため、[GEMINI_API_KEY]セクションもチェック
        if not api_key and "GEMINI_API_KEY" in st.secrets:
            gemini_section = st.secrets["GEMINI_API_KEY"]
            if isinstance(gemini_section, dict) and "GEMINI_API_KEY" in gemini_section:
                api_key = gemini_section["GEMINI_API_KEY"]
            elif isinstance(gemini_section, str):
                api_key = gemini_section
        
        # パターン3: トップレベルに直接設定されている場合
        if not api_key and "GEMINI_API_KEY" in st.secrets:
            if isinstance(st.secrets["GEMINI_API_KEY"], str):
                api_key = st.secrets["GEMINI_API_KEY"]
        
        # パターン4: フラットなキーとして設定されている場合（小文字）
        if not api_key and "gemini_api_key" in st.secrets:
            api_key = st.secrets["gemini_api_key"]
        
        # パターン5: 環境変数から取得
        if not api_key and "GEMINI_API_KEY" in os.environ:
            api_key = os.environ["GEMINI_API_KEY"]
        
        # APIキーが見つからない場合
        if not api_key:
            return None, "Gemini APIキーが見つかりません。Streamlit CloudのSecretsに'[gemini]'セクション内に'api_key = \"YOUR_API_KEY\"'を設定してください。"
        
        # Gemini APIの設定
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # 書き方のスタイル説明
        style_instructions = {
            "標準的": "客観的で読みやすい標準的な文体で書いてください。",
            "評論的": "批判的かつ分析的な視点で、データの意味を深く考察する評論的な文体で書いてください。",
            "詩的": "比喩やイメージを多用し、文学的で詩的な表現を用いた文体で書いてください。",
            "学術的": "学術論文のような形式で、専門用語を使い、論理的に分析する文体で書いてください。",
            "親しみやすい": "読者に語りかけるような親しみやすい口調で、わかりやすく説明する文体で書いてください。"
        }
        style_instruction = style_instructions.get(writing_style, style_instructions["標準的"])
        
        # ユーザーの気づきをプロンプトに含める
        user_insights_section = ""
        if user_insights and user_insights.strip():
            user_insights_section = f"""

【ユーザーの気づき・観察】
{user_insights}

上記のユーザーの気づきや観察も踏まえて、考察記事に反映してください。"""
        
        # プロンプトの作成
        prompt = f"""以下の年齢別書籍数の統計データを分析して、考察記事を書いてください。
特に、「なぜそのような出版傾向になっているのか」という原因や背景を深く考察することが重要です。

【統計データ】
- 総書籍数: {stats.get('総書籍数', 0)}冊
- 平均対象年齢: {stats.get('平均対象年齢', 0):.1f}歳
- 最小年齢: {stats.get('最小年齢', 0)}歳
- 最大年齢: {stats.get('最大年齢', 0)}歳
- 中央値年齢: {stats.get('中央値年齢', 0):.1f}歳
- ピーク年齢: {stats.get('ピーク年齢', 'N/A')}歳（書籍数: {stats.get('ピーク年齢の書籍数', 0)}冊）

【年齢別書籍数（上位10位）】
{chr(10).join([f"- {age}歳: {count}冊" for age, count in stats.get('年齢別書籍数（上位10位）', {}).items()])}

【年齢帯別書籍数】
{chr(10).join([f"- {age_group}: {count}冊" for age_group, count in stats.get('年齢帯別書籍数', {}).items()])}

【年代別書籍数】
{chr(10).join([f"- {decade}: {count}冊" for decade, count in stats.get('年代別書籍数', {}).items()]) if stats.get('年代別書籍数') else "データなし"}{user_insights_section}

以下の構成で、800-1000文字程度の考察記事を書いてください：
1. 導入（データの概要と主要な傾向）
2. 年齢分布の特徴とその背景
   - 特定の年齢層に書籍が集中している理由
   - 社会的・文化的な背景の考察
3. 年代別の傾向とその背景（データがある場合）
   - 時代の変化が出版傾向に与えた影響
5. 総合的な考察とまとめ
   - なぜこのような出版傾向が生まれたのか
   - 社会背景、市場ニーズ、文化的要因などの多角的な分析

記事は読みやすく、データに基づいた具体的な分析を含めてください。
特に、単に「どのような傾向があるか」を述べるだけでなく、「なぜそのような傾向になっているのか」という原因や背景を深く考察することが重要です。
また、{style_instruction}"""
        
        # 記事生成
        response = model.generate_content(prompt)
        article = response.text
        
        return article, None
        
    except Exception as e:
        return None, f"記事の生成中にエラーが発生しました: {str(e)}"

# --- データ読み込み関数 (キャッシュ機能付き) ---
@st.cache_data(ttl=600) # 10分ごとにキャッシュクリア
def load_data():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    
    # 1. Streamlit Secrets (クラウド用) があるか確認
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    
    # 2. なければローカルのJSONファイルを探す
    elif os.path.exists(JSON_FILE):
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, scope)
    
    else:
        st.error("認証情報が見つかりません。secrets.toml または service_account.json を確認してください。")
        return pd.DataFrame()

    # スプシ接続
    client = gspread.authorize(creds)
    try:
        sheet = client.open(SPREADSHEET_NAME).sheet1
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        return df
    except Exception as e:
        st.error(f"スプレッドシートの読み込みに失敗しました: {e}")
        return pd.DataFrame()

# --- メイン処理 ---
with st.spinner('データを読み込んでいます...'):
    df = load_data()

if not df.empty:
    # 列名のマッピング（実際のスプレッドシートの列名に合わせる）
    COL_TITLE = "タイトル"
    COL_AUTHOR = "作成者"
    COL_PUBLISH_DATE = "発行日"
    COL_SUBJECT = "主題"
    
    # 「〇〇歳からの」というパターンのタイトルのみをフィルタリング
    if COL_TITLE in df.columns:
        # 「〇〇歳からの」パターンを含む行のみをフィルタリング
        df = df[df[COL_TITLE].apply(is_kara_pattern)].copy()
        # タイトルから年齢を抽出して列を追加
        df["対象年齢"] = df[COL_TITLE].apply(extract_age)
    
    # データの概要
    col1, col2 = st.columns(2)
    with col1:
        st.metric("収集済み書籍数", f"{len(df)} 冊")
    with col2:
        if "対象年齢" in df.columns:
            valid_ages = df["対象年齢"].dropna()
            if len(valid_ages) > 0:
                avg_age = valid_ages.mean()
                st.metric("平均対象年齢", f"{avg_age:.1f} 歳")
            else:
                st.metric("平均対象年齢", "データなし")
        else:
            st.metric("列数", f"{len(df.columns)} 列")

    # --- タブでコンテンツを分ける ---
    tab1, tab2 = st.tabs(["📊 データ可視化", "📝 考察記事"])
    
    with tab1:
        # --- 1. ヒストグラム (Altairで美しく描画) ---
        st.subheader("📊 年齢ごとの書籍数分布")
        
        if "対象年齢" in df.columns:
            # 年齢データが存在する行のみをフィルタリング
            df_with_age = df[df["対象年齢"].notna()].copy()
            
            if len(df_with_age) > 0:
                # 1歳ごとに集計
                age_counts = df_with_age["対象年齢"].value_counts().sort_index()
                age_df = pd.DataFrame({
                    "対象年齢": age_counts.index,
                    "書籍数": age_counts.values
                })
                
                # Altairチャートの作成（ツールチップ付き、1歳ごとの棒グラフ）
                chart = alt.Chart(age_df).mark_bar().encode(
                    x=alt.X("対象年齢:Q", 
                           title="年齢",
                           axis=alt.Axis(
                               tickMinStep=1,
                               labelAngle=0
                           )),
                    y=alt.Y("書籍数:Q", title="書籍数"),
                    tooltip=[
                        alt.Tooltip("対象年齢:Q", title="年齢", format="d"),
                        alt.Tooltip("書籍数:Q", title="書籍数", format="d")
                    ]
                ).interactive()
                
                st.altair_chart(chart, use_container_width=True)

                if COL_PUBLISH_DATE in df_with_age.columns:
                    # 発行日から年代を抽出
                    df_with_age["年代"] = df_with_age[COL_PUBLISH_DATE].apply(extract_decade)
                    # 年代データが存在する行のみをフィルタリング
                    df_with_decade = df_with_age[df_with_age["年代"].notna()].copy()
                    
                    if len(df_with_decade) > 0:
                        # --- 各年代の各年齢毎の書籍数を表示 ---
                        st.subheader("📊 各年代×対象年齢別の書籍数")
                        
                        # クロス集計表を作成（年代×年齢）
                        cross_table = pd.crosstab(df_with_decade["年代"], df_with_decade["対象年齢"])
                        
                        # グラフ用にlong formatに変換
                        cross_table_long = cross_table.reset_index().melt(
                            id_vars=["年代"],
                            var_name="対象年齢",
                            value_name="書籍数"
                        )
                        cross_table_long = cross_table_long[cross_table_long["書籍数"] > 0]  # 書籍数が0の行を除外
                        cross_table_long["対象年齢"] = cross_table_long["対象年齢"].astype(int)  # 整数型に変換
                        # 古い年代から積み上がるように、年代でソート（昇順）
                        cross_table_long = cross_table_long.sort_values(["対象年齢", "年代"])
                        
                        # 積み上げバーチャート（年代ごとに色分け、古い年代から積み上がる）
                        cross_chart = alt.Chart(cross_table_long).mark_bar(opacity=0.8).encode(
                            x=alt.X("対象年齢:Q", 
                                   title="対象年齢",
                                   axis=alt.Axis(
                                       tickMinStep=1,
                                       labelAngle=0
                                   )),
                            y=alt.Y("書籍数:Q", title="書籍数"),
                            color=alt.Color("年代:N", 
                                           title="年代", 
                                           scale=alt.Scale(scheme="category20"),
                                           sort=alt.SortField("年代", order="ascending")),
                            order=alt.Order("年代:Q", sort="ascending"),
                            tooltip=[
                                alt.Tooltip("年代:N", title="年代"),
                                alt.Tooltip("対象年齢:Q", title="対象年齢", format="d"),
                                alt.Tooltip("書籍数:Q", title="書籍数", format="d")
                            ]
                        )
                        
                        st.altair_chart(cross_chart, use_container_width=True)
                    else:
                        st.info("発行日から年代を抽出できた書籍がありません。")
                else:
                    st.warning(f"発行日列（{COL_PUBLISH_DATE}）が見つかりません。")
            else:
                st.warning("年齢データを抽出できた書籍がありません。タイトルに年齢情報が含まれているか確認してください。")
        else:
            st.warning(f"タイトル列（{COL_TITLE}）が見つかりません。列名を確認してください。")
    
    with tab2:
        # --- サイドバーに考察記事生成のUIを配置 ---
        with st.sidebar:
            st.markdown("---")
            st.subheader("📝 考察記事生成")
            st.markdown("Gemini APIを使用して、年齢別書籍数の統計データから考察記事を自動生成します。")
            
            # 書き方の選択
            writing_style = st.selectbox(
                "記事の書き方",
                ["標準的", "評論的", "詩的", "学術的", "親しみやすい"],
                help="記事の文体やトーンを選択してください"
            )
            
            # ユーザーの気づき入力
            user_insights = st.text_area(
                "気づいたこと・観察したい点",
                placeholder="例：10代向けの書籍が多いことに気づきました。また、自己啓発系のジャンルが目立ちます。",
                help="データを見て気づいたことや、特に考察してほしい点があれば記入してください",
                height=100
            )
            
            # 記事生成ボタン
            if st.button("考察記事を生成", type="primary", use_container_width=True):
                if "対象年齢" in df.columns:
                    df_with_age = df[df["対象年齢"].notna()].copy()
                    if len(df_with_age) > 0:
                        with st.spinner("考察記事を生成中..."):
                            # 統計データを集計
                            stats = aggregate_statistics(df_with_age)
                            
                            # 記事を生成
                            article, error = generate_article_with_gemini(stats, writing_style, user_insights)
                            
                            if error:
                                st.error(error)
                            elif article:
                                # セッションステートに保存
                                st.session_state['generated_article'] = article
                                st.session_state['writing_style'] = writing_style
                                st.success("記事が生成されました！「考察記事」タブで確認できます。")
                                st.rerun()
                            else:
                                st.warning("記事の生成に失敗しました。")
                    else:
                        st.warning("年齢データを抽出できた書籍がありません。")
                else:
                    st.warning("対象年齢のデータがありません。")
        
        # --- 考察記事の表示 ---
        st.subheader("📝 生成された考察記事")
        
        if 'generated_article' in st.session_state:
            if 'writing_style' in st.session_state:
                st.caption(f"書き方: {st.session_state['writing_style']}")
            st.markdown("---")
            st.markdown(st.session_state['generated_article'])
            st.markdown("---")
            
            # 記事をクリアするボタン
            if st.button("記事をクリア", key="clear_article"):
                del st.session_state['generated_article']
                if 'writing_style' in st.session_state:
                    del st.session_state['writing_style']
                st.rerun()
        else:
            st.info("サイドバーから「考察記事を生成」ボタンを押して、記事を生成してください。")
else:
    st.info("データがありません。Jupyter Notebookを実行してデータを収集してください。")