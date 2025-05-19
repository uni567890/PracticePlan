import os
from flask import Flask, render_template, request, redirect, jsonify
from flask_sqlalchemy import SQLAlchemy
import markdown
from google import genai
import json
from datetime import date
import sqlalchemy
import psycopg2

app = Flask(__name__)

# データベースの設定
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", 'sqlite:///dates.db')  # SQLiteデータベースを使用
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# データベースモデル
class DateEntryMember(db.Model):  # 中間テーブルをモデルクラスとして定義
    __tablename__ = 'date_entry_member'  # テーブル名を明示的に指定（任意）
    date_entry_id = db.Column(db.Integer, db.ForeignKey('date_entry.id'), primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), primary_key=True)
    attendance = db.Column(db.String(50), nullable=True)

    # DateEntry と Member へのリレーションシップ
    date_entry = db.relationship("DateEntry", back_populates="member_associations")
    member = db.relationship("Member", back_populates="date_associations")

class DateEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(10), nullable=False)
    max_practice_time = db.Column(db.Integer, nullable=False)
    # members = db.relationship('Member', secondary="date_entry_member", ...) # ← 古い定義を削除
    member_associations = db.relationship("DateEntryMember", back_populates="date_entry", cascade="all, delete-orphan")

class Member(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    part = db.Column(db.Enum('S', 'A', 'T', 'B', name='part_enum'), nullable=False)  # Enum型を使用
    part_detail = db.Column(db.Enum('上', '下', '未設定', name='part_detail_enum'), nullable=True, default='未設定')  # 上下を追加
    skill_level = db.Column(db.Enum('Beginner', 'Intermediate', 'Advanced', '未設定', name='skill_level_enum'), nullable=False, default='未設定')  # Enum型を使用
    date_associations = db.relationship("DateEntryMember", back_populates="member")

    def __repr__(self):
        return f"Member(id={self.id}, name='{self.name}', part='{self.part}', part_detail='{self.part_detail}', skill_level='{self.skill_level}')"

class PracticePlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(10), nullable=False)
    soprano_count = db.Column(db.Integer, nullable=False)
    alto_count = db.Column(db.Integer, nullable=False)
    tenor_count = db.Column(db.Integer, nullable=False)
    bass_count = db.Column(db.Integer, nullable=False)
    max_practice_time = db.Column(db.Integer, nullable=False)  # 最大練習時間（分）
    songs = db.relationship('Song', secondary="practice_plan_song", backref=db.backref('practice_plans', lazy=True))

class PracticePlanTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    template_data = db.Column(db.Text, nullable=False)  # 練習内容をJSON形式で保存
    focused_part = db.Column(db.String(100), nullable=True)  # 注力パート
    focused_section = db.Column(db.String(100), nullable=True)  # 注力セクション

class Song(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    completion_level = db.Column(db.String(50), nullable=True)  # 完成度
    notes = db.Column(db.Text, nullable=True)  # その他考慮事項
    is_div = db.Column(db.Boolean, default=False)  # divかどうか
    difficulty = db.Column(db.String(50), nullable=True)  # 難易度
    practice_frequency = db.Column(db.String(50), nullable=True)  # 練習頻度
    last_practiced = db.Column(db.Date, nullable=True)  # 最終練習日
    practice_count = db.Column(db.Integer, default=0)  # 練習回数

class SongPart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('song.id'), nullable=False)  # 曲ID
    part_name = db.Column(db.String(100), nullable=False)  # 曲の部分名（例: A, B, サビ）
    completion_level = db.Column(db.String(50), nullable=True)  # 完成度
    importance = db.Column(db.String(50), nullable=True)  # 重要度

    # 曲とのリレーション
    song = db.relationship('Song', backref=db.backref('parts', lazy=True))  # backref名を 'parts' に変更

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(10), nullable=False)
    feedback = db.Column(db.Text, nullable=False)

class AISuggestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(10), nullable=False)
    suggestion = db.Column(db.Text, nullable=False)

class Performance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(10), nullable=False)  # 本番の日程
    songs = db.relationship(
        'PerformanceSong',
        backref='performance',
        lazy=True,
        cascade="all, delete-orphan"  # 関連付けられた曲を自動的に削除
    )
    description = db.Column(db.String(200), nullable=True)  # 本番の説明（任意）

class PerformanceSong(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    performance_id = db.Column(db.Integer, db.ForeignKey('performance.id'), nullable=False)
    song_name = db.Column(db.String(100), nullable=False)  # 曲名

# 中間テーブル
practice_plan_song = db.Table('practice_plan_song',
    db.Column('practice_plan_id', db.Integer, db.ForeignKey('practice_plan.id'), primary_key=True),
    db.Column('song_id', db.Integer, db.ForeignKey('song.id'), primary_key=True)
)

# 中間テーブル
# date_entry_member = db.Table('date_entry_member', #  古い中間テーブル定義は削除します。
#     db.Column('date_entry_id', db.Integer, db.ForeignKey('date_entry.id'), primary_key=True),
#     db.Column('member_id', db.Integer, db.ForeignKey('member.id'), primary_key=True),
#     db.Column('attendance', db.String(50), nullable=True)  # 出席状況 (例: Present, Absent, Late)
# )

# 初回実行時にデータベースを作成
with app.app_context():
    db.create_all()

@app.route("/")
def index():
    dates = DateEntry.query.all()
    performances = Performance.query.all()

    events = []
    for date in dates:
        events.append({
            "title": "練習日",
            "start": date.date,
            "color": "blue"
        })
    for performance in performances:
        events.append({
            "title": "本番日程",
            "start": performance.date,
            "color": "red"
        })

    calendar_events_data = json.dumps(events)  # json.dumpsを使用
    songs = Song.query.all()
    return render_template("index.html", calendar_events_data=calendar_events_data, songs=songs, dates=dates)

@app.route("/submit", methods=["POST"])
def submit_date():
    date = request.form.get("date")  # フォームから送信された日付を取得
    if date:
        # データベースに保存
        new_entry = DateEntry(date=date)
        db.session.add(new_entry)
        db.session.commit()
    return render_template("result.html", date=date)  # データをresult.htmlに渡す

@app.route("/dates")
def show_dates():
    dates = DateEntry.query.all()  # データベースからすべてのデータを取得
    return render_template("dates.html", dates=dates)

@app.route("/delete/<int:id>", methods=["POST"])
def delete_date(id):
    # 指定されたIDのデータを取得
    entry = DateEntry.query.get_or_404(id)
    # データベースから削除
    db.session.delete(entry)
    db.session.commit()
    return "Entry deleted successfully", 200

@app.route("/delete_date/<int:date_id>", methods=["POST"])
def delete_practice_date(date_id):  # 関数名を変更
    date_entry = DateEntry.query.get_or_404(date_id)  # 指定されたIDの練習日を取得

    # データベースから削除
    db.session.delete(date_entry)
    db.session.commit()
    return redirect("/dates")  # 削除後、練習日一覧ページにリダイレクト

@app.route("/submit_plan", methods=["POST"])
def submit_plan():
    # フォームデータを取得
    date = request.form.get("date")
    additional_prompt = request.form.get("additional_prompt", "").strip()  # 追加のプロンプトを取得
    template_id = request.form.get("template_id")  # テンプレートIDを取得

    # 練習日に対応するデータを取得
    date_entry = DateEntry.query.filter_by(date=date).first()
    if not date_entry:
        return "指定された練習日が見つかりませんでした。", 404

    # テンプレートが選択されている場合は、テンプレートデータを適用
    if template_id:
        template = PracticePlanTemplate.query.get(template_id)
        if template:
            try:
                template_data = json.loads(template.template_data)
                # テンプレートデータを適用する処理を実装
                # 例：曲のリスト、練習時間などを設定
            except json.JSONDecodeError:
                return "テンプレートデータの形式が正しくありません。", 400

            focused_part = template.focused_part
            focused_section = template.focused_section
        else:
            focused_part = None
            focused_section = None
    else:
        focused_part = None
        focused_section = None

    # 本番の日程と曲目を取得
    performances = Performance.query.all()
    performance_details = []
    for performance in performances:
        songs = [song.song_name for song in performance.songs]
        performance_details.append({
            "date": performance.date,
            "songs": songs
        })

    # 本番ごとの曲情報をフォーマット
    performance_info = "\n".join([
        f"- {detail['date']}: {', '.join(detail['songs'])}" for detail in performance_details
    ])

    # その他のデータベース情報を取得
    all_songs = Song.query.all()
    all_feedback = Feedback.query.all()
    all_suggestions = AISuggestion.query.all()

    # AIに渡すプロンプトを作成
    songs_info = ""
    for song in all_songs:
        songs_info += f"- {song.name} ({song.completion_level or '未設定'})"
        for part in song.parts:
            songs_info += f"\n  - {part.part_name}: {part.completion_level or '未設定'} (重要度: {part.importance or '未設定'})"
        songs_info += "\n"

    feedback_info = "\n".join([f"- {feedback.date}: {feedback.feedback}" for feedback in all_feedback])
    suggestions_info = "\n".join([f"- {suggestion.date}: {suggestion.suggestion}" for suggestion in all_suggestions])

    # 他の練習日情報を取得
    all_dates = DateEntry.query.all()
    # 選択した練習日から2か月後までの日程をフィルタリング
    from datetime import datetime, timedelta
    selected_date = datetime.strptime(date, "%Y-%m-%d").date()
    two_months_later = selected_date + timedelta(days=60)
    filtered_dates = [d for d in all_dates if datetime.strptime(d.date, "%Y-%m-%d").date() >= selected_date and datetime.strptime(d.date, "%Y-%m-%d").date() <= two_months_later]
    dates_info = "\n".join([f"- {d.date}" for d in filtered_dates])

    # 参加メンバー情報を取得
    date_entry = DateEntry.query.filter_by(date=date).first()
    member_info = ""
    if date_entry:
        for member_association in date_entry.member_associations:
            member = member_association.member
            member_info += f"- {member.name} ({member.part}, {member.skill_level})\n"

    prompt = f"""
あなたは合唱練習計画アプリのアシスタントです。
以下の詳細に基づいて、練習計画を提案してください：

**練習日に関する情報:**
- 練習日: {date}
- 最大練習時間: {date_entry.max_practice_time} 分

**本番に関する情報:**
{performance_info}

**曲に関する情報:**
{songs_info}

**過去のフィードバック:**
{feedback_info}

**過去のAI提案:**
{suggestions_info}

**今後の練習日程:**
{dates_info}

**練習テンプレート:**
- 注力パート: {focused_part or "なし"}
- 注力セクション: {focused_section or "なし"}

**参加メンバー:**
{member_info or "なし"}

**ユーザーからの追加指示:**
{additional_prompt}

**指示:**
- 上記の情報を総合的に判断し、以下の要素を考慮して詳細な練習計画を提案してください。
    - 注力パートと注力セクション
    - 曲の部分ごとの完成度と重要度
    - 参加メンバーのパート（ソプラノ、アルト、テノール、バス）、パート詳細（上、下、未設定）、スキルレベル（初心者、中級者、上級者、未設定）
    - 練習時間

- 練習計画は、当日のタイムスケジュールとして、改行してリスト形式で出力してください。
"""

    # AI提案を生成
    ai_suggestion = generate_ai_suggestion(prompt)

    # 提案をデータベースに保存
    new_suggestion = AISuggestion(date=date, suggestion=ai_suggestion)
    db.session.add(new_suggestion)
    db.session.commit()

    # 提案を新しいページに渡す
    return render_template("ai_suggestion.html", date=date, suggestion=ai_suggestion)

@app.route("/revise_plan", methods=["POST"])
def revise_plan():
    date = request.form.get("date")
    current_suggestion = request.form.get("current_suggestion")
    revision_prompt = request.form.get("revision_prompt", "").strip()

    # 練習日に対応するデータを取得
    date_entry = DateEntry.query.filter_by(date=date).first()
    if not date_entry:
        return "指定された練習日が見つかりませんでした。", 404

    # 本番の日程と曲目を取得
    performances = Performance.query.all()
    performance_details = []
    for performance in performances:
        songs = [song.song_name for song in performance.songs]
        performance_details.append({
            "date": performance.date,
            "songs": songs
        })

    # 本番ごとの曲情報をフォーマット
    performance_info = "\n".join([
        f"- {detail['date']}: {', '.join(detail['songs'])}" for detail in performance_details
    ])

    # その他のデータベース情報を取得
    all_songs = Song.query.all()
    all_feedback = Feedback.query.all()
    all_suggestions = AISuggestion.query.all()

    # AIに渡すプロンプトを作成
    songs_info = ""
    for song in all_songs:
        songs_info += f"- {song.name} ({song.completion_level or '未設定'})"
        for part in song.parts:
            songs_info += f"\n  - {part.part_name}: {part.completion_level or '未設定'}"
        songs_info += "\n"

    feedback_info = "\n".join([f"- {feedback.date}: {feedback.feedback}" for feedback in all_feedback])
    suggestions_info = "\n".join([f"- {suggestion.date}: {suggestion.suggestion}" for suggestion in all_suggestions])

    # 他の練習日情報を取得
    all_dates = DateEntry.query.all()
    # 選択した練習日から2か月後までの日程をフィルタリング
    from datetime import datetime, timedelta
    selected_date = datetime.strptime(date, "%Y-%m-%d").date()
    two_months_later = selected_date + timedelta(days=60)
    filtered_dates = [d for d in all_dates if datetime.strptime(d.date, "%Y-%m-%d").date() >= selected_date and datetime.strptime(d.date, "%Y-%m-%d").date() <= two_months_later]
    dates_info = "\n".join([f"- {d.date}" for d in filtered_dates])

    prompt = f"""
    あなたは合唱練習計画アプリのアシスタントです。
    以前に提案した練習計画を修正します。
    以下の詳細に基づいて、練習計画を修正してください：

    - 練習日: {date}
    - 最大練習時間: {date_entry.max_practice_time} 分

    本番の日程と演奏曲目:
    {performance_info}

    登録されているすべての曲 (部分ごとの完成度を含む):
    {songs_info}

    過去のフィードバック:
    {feedback_info}

    過去のAI提案:
    {suggestions_info}

    選択した練習日から2か月後までの練習日程:
    {dates_info}

    現在の練習計画:
    {current_suggestion}

    ユーザーからの修正指示:
    {revision_prompt}

    これらの情報を総合的に判断し、**曲の部分ごとの完成度**や練習時間を考慮した詳細な練習計画を修正してください。
    また、当日のタイムスケジュールは、改行してリスト形式で出力してください。
    """

    # AI提案を生成
    ai_suggestion = generate_ai_suggestion(prompt)

    # 提案をデータベースに保存 (既存の提案を更新するか、新しい提案として追加するかは要検討)
    new_suggestion = AISuggestion(date=date, suggestion=ai_suggestion)
    db.session.add(new_suggestion)
    db.session.commit()

    # 提案を新しいページに渡す
    return render_template("ai_suggestion.html", date=date, suggestion=ai_suggestion)

@app.route("/add_song", methods=["GET", "POST"])
def add_song():
    if request.method == "POST":
        song_name = request.form.get("song_name")
        completion_level = request.form.get("completion_level")
        notes = request.form.get("notes")
        is_div = request.form.get("is_div") == "on"
        difficulty = request.form.get("difficulty")
        practice_frequency = request.form.get("practice_frequency")
        last_practiced_str = request.form.get("last_practiced")

        if song_name:
            # Convert last_practiced to a date object
            last_practiced = None
            if last_practiced_str:
                last_practiced = date.fromisoformat(last_practiced_str)

            # 曲情報をデータベースに保存
            new_song = Song(
                name=song_name,
                completion_level=completion_level,
                notes=notes,
                is_div=is_div,
                difficulty=difficulty,
                practice_frequency=practice_frequency,
                last_practiced=last_practiced
            )
            db.session.add(new_song)
            db.session.commit()

            # 曲の部分を保存
            part_names = request.form.getlist("part_names[]")
            part_completion_levels = request.form.getlist("part_completion_levels[]")
            for part_name, part_completion_level in zip(part_names, part_completion_levels):
                new_part = SongPart(song_id=new_song.id, part_name=part_name, completion_level=part_completion_level)
                db.session.add(new_part)
            db.session.commit()

            return render_template("add_song.html", message="曲が正常に追加されました！", songs=Song.query.all())
    return render_template("add_song.html", songs=Song.query.all())

@app.route("/delete_song/<int:song_id>", methods=["POST"])
def delete_song(song_id):
    song = Song.query.get(song_id)
    if song:
        db.session.delete(song)
        db.session.commit()
        return redirect("/add_song")
    return "曲が見つかりませんでした。", 404

@app.route("/edit_song/<int:song_id>", methods=["GET", "POST"])
def edit_song(song_id):
    song = Song.query.get(song_id)
    if not song:
        return "曲が見つかりませんでした。", 404

    if request.method == "POST":
        # Update song details
        song.name = request.form.get("song_name")
        song.completion_level = request.form.get("completion_level")
        song.notes = request.form.get("notes")
        song.is_div = request.form.get("is_div") == "on"
        song.difficulty = request.form.get("difficulty")
        song.practice_frequency = request.form.get("practice_frequency")
        last_practiced_str = request.form.get("last_practiced")

        # Convert last_practiced to a date object
        last_practiced = None
        if last_practiced_str:
            last_practiced = date.fromisoformat(last_practiced_str)

        song.last_practiced = last_practiced

        # Update song parts
        part_ids = request.form.getlist("part_ids[]")
        part_names = request.form.getlist("part_names[]")
        part_completion_levels = request.form.getlist("part_completion_levels[]")
        part_importances = request.form.getlist("part_importances[]")

        for part_id, part_name, part_completion_level, part_importance in zip(part_ids, part_names, part_completion_levels, part_importances):
            if part_id == "new":
                # Add new part
                new_part = SongPart(song_id=song.id, part_name=part_name, completion_level=part_completion_level, importance=part_importance)
                db.session.add(new_part)
            else:
                # Update existing part
                part = SongPart.query.get(int(part_id))
                if part:
                    part.part_name = part_name
                    part.completion_level = part_completion_level
                    part.importance = part_importance

        db.session.commit()
        return redirect("/add_song")

    return render_template("edit_song.html", song=song)

@app.route("/submit_feedback", methods=["POST"])
def submit_feedback():
    # フィードバックデータを取得
    date = request.form.get("date")
    feedback_text = request.form.get("feedback")

    # フィードバックをデータベースに保存
    new_feedback = Feedback(date=date, feedback=feedback_text)
    db.session.add(new_feedback)
    db.session.commit()

    # フィードバックを受け取ったことを通知
    return render_template("feedback_received.html", date=date)

@app.route("/add_date", methods=["GET", "POST"])
def add_date():
    members_all = Member.query.all()

    if request.method == "POST":
        date_str = request.form.get("date")
        max_practice_time = request.form.get("max_practice_time")
        member_ids_str_list = request.form.getlist("member_ids[]")

        if all([date_str, max_practice_time]):
            # 既存のDateEntryを探すか、新規作成するかのロジックを推奨
            # ここでは簡略化のため、毎回新規作成するとして進めますが、
            # 同じ日付の練習は一つにまとめる方が良いかもしれません。
            new_date_entry = DateEntry(
                date=date_str,
                max_practice_time=int(max_practice_time)
            )
            db.session.add(new_date_entry)
            # db.session.flush() # この段階では必須ではない。IDはコミット時に確定。

            # 既存の関連をクリアする場合 (もしこのDateEntryが既存のものを更新する場合)
            # new_date_entry.member_associations.clear() # これで関連するDateEntryMemberオブジェクトが削除される (cascadeによる)
            # しかし、新規作成なので、通常はクリアは不要。

            processed_member_ids = set()  # 重複処理を避けるため

            for member_id_str in member_ids_str_list:
                member_id = int(member_id_str)
                if member_id in processed_member_ids:
                    continue  # 同じメンバーIDが複数送られてきた場合はスキップ
                processed_member_ids.add(member_id)

                member = Member.query.get(member_id)
                if member:
                    attendance = request.form.get(f"attendance_{member_id}")

                    # 既存の関連がないか確認 (または、更新なら既存のものを取得)
                    # ここでは新規追加のみを考慮
                    association = DateEntryMember(
                        date_entry=new_date_entry,  # DateEntryオブジェクトを渡す
                        member=member,  # Memberオブジェクトを渡す
                        attendance=attendance
                    )
                    db.session.add(association)

            try:
                db.session.commit()
                message = "練習日が正常に登録されました！"
            except sqlalchemy.exc.IntegrityError:
                db.session.rollback()
                message = "エラー：データの登録に失敗しました。重複したメンバー登録の可能性があります。"
            except Exception as e:
                db.session.rollback()
                message = f"予期せぬエラーが発生しました: {str(e)}"

            return render_template("add_date.html", message=message, dates=DateEntry.query.all(), members=members_all)
    return render_template("add_date.html", dates=DateEntry.query.all(), members=members_all)

@app.route("/edit_date/<int:date_id>", methods=["GET", "POST"])
def edit_date(date_id):
    date_entry = DateEntry.query.get_or_404(date_id)
    members = Member.query.all()

    if request.method == "POST":
        date_entry.date = request.form.get("date")
        date_entry.max_practice_time = int(request.form.get("max_practice_time"))
        member_ids = request.form.getlist("member_ids[]")

        # Clear existing members
        # date_entry.members.clear() # AttributeError: 'DateEntry' object has no attribute 'members'
        date_entry.member_associations.clear()

        # Add selected members
        for member_id in member_ids:
            member = Member.query.get(member_id)
            if member:
                # date_entry.members.append(member) # AttributeError: 'DateEntry' object has no attribute 'members'
                attendance = request.form.get(f"attendance_{member_id}")
                association = DateEntryMember(date_entry=date_entry, member=member, attendance=attendance)
                db.session.add(association)

        db.session.commit()
        return redirect("/dates")

    return render_template("edit_date.html", date_entry=date_entry, members=members)

@app.route("/add_performance", methods=["GET", "POST"])
def add_performance():
    if request.method == "POST":
        date = request.form.get("date")
        song_ids = request.form.getlist("song_ids[]")

        if date and song_ids:
            # 本番の日程をデータベースに保存
            new_performance = Performance(date=date)
            db.session.add(new_performance)
            db.session.commit()

            # 演奏する曲を保存
            for song_id in song_ids:
                song = Song.query.get(song_id)
                if song:
                    new_performance_song = PerformanceSong(performance_id=new_performance.id, song_name=song.name)
                    db.session.add(new_performance_song)
            db.session.commit()

            return render_template("add_performance.html", message="本番日程が正常に登録されました！", performances=Performance.query.all(), songs=Song.query.all())
    return render_template("add_performance.html", performances=Performance.query.all(), songs=Song.query.all())

@app.route("/edit_performance/<int:performance_id>", methods=["GET", "POST"])
def edit_performance(performance_id):
    performance = Performance.query.get_or_404(performance_id)
    all_songs = Song.query.all()  # すべての曲を取得

    if request.method == "POST":
        # フォームから送信されたデータを取得
        performance.date = request.form.get("date")
        performance.description = request.form.get("description")

        # 演奏曲目を更新
        selected_song_ids = request.form.getlist("song_ids")
        selected_songs = [Song.query.get(int(song_id)) for song_id in selected_song_ids]

        # 関連付けを更新
        performance.songs.clear()  # 現在の関連付けをクリア
        performance.songs.extend(selected_songs)  # 新しい関連付けを追加

        # データベースに保存
        db.session.commit()
        return redirect("/add_performance")

    return render_template("edit_performance.html", performance=performance, all_songs=all_songs)

@app.route("/delete_performance/<int:performance_id>", methods=["POST"])
def delete_performance(performance_id):
    performance = Performance.query.get_or_404(performance_id)  # 指定されたIDの本番日程を取得

    # データベースから削除
    db.session.delete(performance)
    db.session.commit()
    return redirect("/add_performance")  # 削除後、本番日程一覧ページにリダイレクト

@app.route("/view_ai_suggestions")
def view_ai_suggestions():
    suggestions = AISuggestion.query.all()  # データベースからすべてのAI提案を取得
    return render_template("view_ai_suggestions.html", suggestions=suggestions)

@app.route("/view_ai_suggestion/<date>")
def view_ai_suggestion(date):
    suggestion = AISuggestion.query.filter_by(date=date).first()
    if not suggestion:
        return "指定された日付の提案が見つかりませんでした。", 404
    return render_template("ai_suggestion.html", date=suggestion.date, suggestion=suggestion.suggestion)

@app.route("/add_template")
def add_template():
    return render_template("add_template.html")

@app.route("/save_template", methods=["POST"])
def save_template():
    name = request.form.get("name")
    description = request.form.get("description")
    template_data = request.form.get("template_data")
    focused_part = request.form.get("focused_part")
    focused_section = request.form.get("focused_section")

    if name and template_data:
        new_template = PracticePlanTemplate(name=name, description=description, template_data=template_data, focused_part=focused_part, focused_section=focused_section)
        db.session.add(new_template)
        db.session.commit()
        return redirect("/")  # リダイレクト先は適宜変更
    return "テンプレートの保存に失敗しました。", 400

@app.route("/edit_template/<int:template_id>")
def edit_template(template_id):
    template = PracticePlanTemplate.query.get_or_404(template_id)
    return render_template("edit_template.html", template=template)

@app.route("/update_template/<int:template_id>", methods=["POST"])
def update_template(template_id):
    template = PracticePlanTemplate.query.get_or_404(template_id)

    template.name = request.form.get("name")
    template.description = request.form.get("description")
    template.template_data = request.form.get("template_data")
    template.focused_part = request.form.get("focused_part")
    template.focused_section = request.form.get("focused_section")

    db.session.commit()
    return redirect("/")  # リダイレクト先は適宜変更

@app.route("/add_member")
def add_member():
    return render_template("add_member.html")

@app.route("/save_member", methods=["POST"])
def save_member():
    name = request.form.get("name")
    part = request.form.get("part")
    skill_level = request.form.get("skill_level")

    if name:
        new_member = Member(name=name, part=part, skill_level=skill_level)
        db.session.add(new_member)
        db.session.commit()
        return redirect("/")  # リダイレクト先は適宜変更
    return "メンバーの保存に失敗しました。", 400

@app.route("/edit_member/<int:member_id>")
def edit_member(member_id):
    member = Member.query.get_or_404(member_id)
    return render_template("edit_member.html", member=member)

@app.route("/update_member/<int:member_id>", methods=["POST"])
def update_member(member_id):
    member = Member.query.get_or_404(member_id)
    member.name = request.form.get("name")
    member.part = request.form.get("part")
    member.part_detail = request.form.get("part_detail")
    member.skill_level = request.form.get("skill_level")
    db.session.commit()
    return redirect("/members")  # メンバー一覧ページにリダイレクト

@app.route("/delete_member/<int:member_id>", methods=["POST"])
def delete_member(member_id):
    member = Member.query.get_or_404(member_id)
    db.session.delete(member)
    db.session.commit()
    return redirect("/members")  # メンバー一覧ページにリダイレクト

@app.route("/members")
def members():
    members = Member.query.all()
    print(members)  # ログに出力
    return render_template("members.html", members=members)

@app.route("/get_date_info")
def get_date_info():
    date_str = request.args.get("date")
    date_entry = DateEntry.query.filter_by(date=date_str).first()

    if date_entry:
        # 練習日情報をJSON形式で返す
        date_info = {
            "id": date_entry.id,
            "date": date_entry.date,
            "max_practice_time": date_entry.max_practice_time,
            "members": [{"id": member.id, "name": member.name, "part": member.part, "skill_level": member.skill_level} for member in date_entry.members]
        }
        return jsonify(date_info)
    else:
        return jsonify({"message": "練習日が見つかりませんでした。"}), 404  # JSON形式のエラーメッセージを返す

def generate_ai_suggestion(prompt):
    # Gemini APIのエンドポイントとAPIキー
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    # APIリクエストを送信
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[prompt]
    )

    html_output = markdown.markdown(response.text)  # レスポンスをHTMLに変換

    # レスポンスから提案を取得
    return html_output

@app.route("/add_members_bulk", methods=["GET", "POST"])
def add_members_bulk():
    if request.method == "POST":
        member_data = request.form.get("member_data")
        if member_data:
            try:
                # メンバーデータを解析
                members = []
                for line in member_data.strip().split("\n"):
                    parts = line.split("\t")  # タブ区切りを想定
                    if len(parts) == 2:
                        part, name = parts
                        part = part.strip()
                        name = name.strip()
                        # パートのバリデーション
                        if part in ['S', 'A', 'T', 'B']:
                            members.append({"part": part, "name": name})

                # メンバーを一括で登録
                for member_info in members:
                    new_member = Member(
                        name=member_info["name"],
                        part=member_info["part"],
                        skill_level="未設定"  # デフォルト値
                    )
                    db.session.add(new_member)
                db.session.commit()
                return render_template("add_members_bulk.html", message="メンバーが一括で登録されました！")
            except Exception as e:
                db.session.rollback()
                return render_template("add_members_bulk.html", error=f"エラーが発生しました: {str(e)}")
        else:
            return render_template("add_members_bulk.html", error="メンバーデータが入力されていません。")
    return render_template("add_members_bulk.html")

if __name__ == "__main__":
    app.run(debug=os.environ.get("DEBUG") == "True")