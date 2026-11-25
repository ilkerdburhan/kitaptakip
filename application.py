import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, flash, redirect, render_template, request, session, url_for

from db import get_db, init_db
from scraper import (
    SearchUnavailableError,
    download_cover,
    get_book_details,
    search_books,
)


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "yerel-gelistirme-anahtari")

TZ = ZoneInfo("Europe/Istanbul")
init_db()


def now():
    return datetime.now(TZ)


def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def get_book(book_id):
    conn = get_db()
    book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    conn.close()
    return book


def estimate_finish(book):
    conn = get_db()
    logs = conn.execute(
        "SELECT pages_read, logged_at FROM reading_logs "
        "WHERE book_id = ? AND logged_at >= ? "
        "ORDER BY logged_at ASC",
        (book["id"], (now() - timedelta(days=7)).isoformat()),
    ).fetchall()
    latest = conn.execute(
        "SELECT COALESCE(MAX(pages_read), 0) AS pages FROM reading_logs WHERE book_id = ?",
        (book["id"],),
    ).fetchone()
    conn.close()
    if not logs:
        return None
    start_row = get_db()
    previous = start_row.execute(
        "SELECT COALESCE(MAX(pages_read), 0) AS pages "
        "FROM reading_logs WHERE book_id = ? AND logged_at < ?",
        (book["id"], logs[0]["logged_at"]),
    ).fetchone()
    start_row.close()
    start_pages = previous["pages"]
    pages = max(0, latest["pages"] - start_pages)
    first_date = datetime.fromisoformat(logs[0]["logged_at"]).date()
    days = max(1, (now().date() - first_date).days + 1)
    speed = pages / days
    if speed <= 0:
        return None
    remaining = max(0, book["page_count"] - latest["pages"])
    return round(remaining / speed) if remaining else 0


def goal_status(completed, goal):
    difference = completed - goal
    if difference >= 0:
        return f"Bu yıl {completed}/{goal}; hedefi {difference} kitap geçtin"
    return f"Bu yıl {completed}/{goal}; hedefin {-difference} kitap gerisindesin"


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == "test" and request.form.get("password") == "123":
            session["logged_in"] = True
            return redirect(url_for("library"))
        flash("Kullanıcı adı veya parola hatalı.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def library():
    status = request.args.get("raf", "okunuyor")
    if status not in ("okunacak", "okunuyor", "okundu"):
        status = "okunuyor"
    conn = get_db()
    books = conn.execute(
        "SELECT b.* FROM books b "
        "LEFT JOIN reading_logs r ON r.book_id = b.id "
        "WHERE b.status = ? "
        "GROUP BY b.id "
        "ORDER BY b.created_at DESC",
        (status,),
    ).fetchall()
    progress = {}
    latest_logs = {}
    for book in books:
        row = conn.execute(
            "SELECT COALESCE(MAX(pages_read), 0) AS pages "
            "FROM reading_logs WHERE book_id = ?",
            (book["id"],),
        ).fetchone()
        progress[book["id"]] = row["pages"]
        latest_log = conn.execute(
            "SELECT id, pages_read, logged_at FROM reading_logs "
            "WHERE book_id = ? ORDER BY logged_at DESC, id DESC LIMIT 1",
            (book["id"],),
        ).fetchone()
        latest_logs[book["id"]] = latest_log
    conn.close()
    estimates = {book["id"]: estimate_finish(book) for book in books}
    return render_template(
        "library.html",
        books=books,
        progress=progress,
        estimates=estimates,
        latest_logs=latest_logs,
        active_status=status,
    )


@app.route("/ara", methods=["GET", "POST"])
@login_required
def search():
    results = []
    message = ""
    manual = False
    query = request.args.get("q", "").strip()
    if request.method == "POST":
        query = request.form.get("q", "").strip()

    if query:
        try:
            results = search_books(query)
            if not results:
                message = "Kitap bulunamadı, elle ekleyebilirsin"
                manual = True
        except SearchUnavailableError:
            message = "Şu an kitap aranamıyor, elle ekleyebilirsin"
            manual = True
    return render_template("search.html", query=query, results=results, message=message, manual=manual)


@app.route("/ekle/scrape", methods=["POST"])
@login_required
def add_scraped():
    data = get_book_details({
        "title": request.form.get("title", "").strip(),
        "author": request.form.get("author", "").strip(),
        "publisher": request.form.get("publisher", "").strip(),
        "cover_url": request.form.get("cover_url", "").strip(),
        "source_url": request.form.get("source_url", "").strip(),
    })
    page_count = int(request.form.get("page_count") or data.get("page_count") or 0)
    if not data["title"] or page_count <= 0:
        flash("Kitap adı ve sayfa sayısı zorunlu; sayfa sayısı sıfırdan büyük olmalıdır.", "danger")
        return redirect(url_for("search", q=request.args.get("q", "")))
    return add_book_record(
        data["title"], data["author"], data["publisher"],
        page_count, data.get("isbn", ""), data.get("publication_year", ""),
        data["source_url"], data.get("cover_url", ""), request.form.get("status", "okunacak")
    )


@app.route("/ekle", methods=["POST"])
@login_required
def add_manual():
    try:
        page_count = int(request.form.get("page_count", "0"))
    except ValueError:
        page_count = 0
    return add_book_record(
        request.form.get("title", "").strip(),
        request.form.get("author", "").strip(),
        request.form.get("publisher", "").strip(),
        page_count,
        request.form.get("isbn", "").strip(),
        request.form.get("publication_year", "").strip(),
        request.form.get("source_url", "").strip(),
        "",
        request.form.get("status", "okunacak"),
    )


def add_book_record(title, author, publisher, page_count, isbn, publication_year, source_url, cover_url, status):
    if not title or page_count <= 0:
        flash("Kitap adı ve sayfa sayısı zorunlu; sayfa sayısı sıfırdan büyük olmalıdır.", "danger")
        return redirect(url_for("search"))
    if status not in ("okunacak", "okunuyor", "okundu"):
        status = "okunacak"

    conn = get_db()
    duplicate = conn.execute(
        "SELECT id FROM books WHERE "
        "(? <> '' AND isbn = ?) OR "
        "(? <> '' AND source_url = ?) OR "
        "(LOWER(title) = LOWER(?) AND LOWER(author) = LOWER(?))",
        (isbn, isbn, source_url, source_url, title, author),
    ).fetchone()
    if duplicate:
        conn.close()
        flash("Bu kitap zaten kitaplığında.", "warning")
        return redirect(url_for("library"))

    finished_at = now().isoformat() if status == "okundu" else None
    cursor = conn.execute(
        "INSERT INTO books "
        "(title, author, publisher, cover, source_url, page_count, isbn, publication_year, status, finished_at, created_at) "
        "VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)",
        (title, author, publisher, source_url, page_count, isbn, publication_year, status, finished_at, now().isoformat()),
    )
    book_id = cursor.lastrowid
    cover_filename = download_cover(cover_url, book_id) if cover_url else ""
    if cover_filename:
        conn.execute("UPDATE books SET cover = ? WHERE id = ?", (cover_filename, book_id))
    conn.commit()
    conn.close()
    flash("Kitap kitaplığınıza eklendi.", "success")
    return redirect(url_for("library", raf=status))


@app.route("/kitap/<int:book_id>/durum", methods=["POST"])
@login_required
def change_status(book_id):
    status = request.form.get("status")
    if status not in ("okunacak", "okunuyor", "okundu"):
        flash("Geçersiz raf.", "danger")
        return redirect(url_for("library"))
    conn = get_db()
    finished_at = now().isoformat() if status == "okundu" else None
    conn.execute("UPDATE books SET status = ?, finished_at = ? WHERE id = ?", (status, finished_at, book_id))
    conn.commit()
    conn.close()
    return redirect(url_for("library", raf=status))


@app.route("/kitap/<int:book_id>/ilerleme", methods=["POST"])
@login_required
def add_progress(book_id):
    book = get_book(book_id)
    if not book:
        flash("Kitap bulunamadı.", "danger")
        return redirect(url_for("library"))
    try:
        pages = int(request.form.get("pages", "0"))
    except ValueError:
        pages = -1
    conn = get_db()
    previous = conn.execute(
        "SELECT COALESCE(MAX(pages_read), 0) AS pages FROM reading_logs WHERE book_id = ?",
        (book_id,),
    ).fetchone()["pages"]
    conn.close()
    if pages < previous:
        flash("Okunan sayfa önceki kayıt düzeyinden küçük olamaz.", "danger")
        return redirect(url_for("library", raf=book["status"]))
    if pages > book["page_count"]:
        flash("Okunan sayfa toplam sayfa sayısını aşamaz.", "danger")
        return redirect(url_for("library", raf=book["status"]))

    status = "okundu" if pages == book["page_count"] else "okunuyor"
    finished_at = now().isoformat() if status == "okundu" else None
    conn = get_db()
    conn.execute(
        "INSERT INTO reading_logs (book_id, pages_read, logged_at) VALUES (?, ?, ?)",
        (book_id, pages, now().isoformat()),
    )
    conn.execute(
        "UPDATE books SET status = ?, finished_at = ? WHERE id = ?",
        (status, finished_at, book_id),
    )
    conn.commit()
    conn.close()
    flash("Okuma ilerlemesi kaydedildi.", "success")
    return redirect(url_for("library", raf=status))


@app.route("/kitap/<int:book_id>/log/<int:log_id>/sil", methods=["POST"])
@login_required
def delete_log(book_id, log_id):
    conn = get_db()
    conn.execute("DELETE FROM reading_logs WHERE id = ? AND book_id = ?", (log_id, book_id))
    book = conn.execute("SELECT page_count, status FROM books WHERE id = ?", (book_id,)).fetchone()
    latest = conn.execute(
        "SELECT COALESCE(MAX(pages_read), 0) AS pages FROM reading_logs WHERE book_id = ?",
        (book_id,),
    ).fetchone()
    if book:
        status = "okundu" if latest["pages"] == book["page_count"] else (
            "okunuyor" if latest["pages"] > 0 else "okunacak"
        )
        finished_at = now().isoformat() if status == "okundu" else None
        conn.execute("UPDATE books SET status = ?, finished_at = ? WHERE id = ?", (status, finished_at, book_id))
    conn.commit()
    conn.close()
    flash("Son okuma kaydı silindi.", "success")
    return redirect(url_for("library"))


@app.route("/kitap/<int:book_id>/puan", methods=["POST"])
@login_required
def rate_book(book_id):
    try:
        rating = int(request.form.get("rating", "0"))
    except ValueError:
        rating = 0
    rating = max(0, min(5, rating))
    conn = get_db()
    conn.execute(
        "UPDATE books SET rating = ?, note = ? WHERE id = ?",
        (rating, request.form.get("note", "").strip(), book_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("library", raf=request.form.get("status", "okundu")))


@app.route("/istatistik")
@login_required
def statistics():
    conn = get_db()
    goal = conn.execute("SELECT yearly_goal FROM settings WHERE id = 1").fetchone()["yearly_goal"]
    year_start = now().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    completed = conn.execute(
        "SELECT COUNT(*) AS count FROM books "
        "WHERE status = 'okundu' AND finished_at >= ?",
        (year_start,),
    ).fetchone()["count"]

    monthly = [0] * 12
    for month in range(1, 13):
        prefix = f"{now().year}-{month:02d}-"
        row = conn.execute(
            "SELECT COALESCE(SUM(pages_read), 0) AS total "
            "FROM reading_logs WHERE substr(logged_at, 1, 7) = ?",
            (f"{now().year}-{month:02d}",),
        ).fetchone()
        monthly[month - 1] = row["total"]

    streak = 0
    log_days = conn.execute(
        "SELECT DISTINCT substr(logged_at, 1, 10) AS day "
        "FROM reading_logs ORDER BY day DESC"
    ).fetchall()
    conn.close()
    day_set = {row["day"] for row in log_days}
    cursor = now().date()
    while cursor.isoformat() in day_set:
        streak += 1
        cursor -= timedelta(days=1)

    status_text = goal_status(completed, goal)
    return render_template(
        "statistics.html",
        goal=goal,
        completed=completed,
        status_text=status_text,
        monthly=monthly,
        streak=streak,
    )


@app.route("/ayarlar", methods=["GET", "POST"])
@login_required
def settings():
    conn = get_db()
    if request.method == "POST":
        try:
            goal = int(request.form.get("yearly_goal", "24"))
        except ValueError:
            goal = 24
        if goal < 1:
            goal = 24
        conn.execute("UPDATE settings SET yearly_goal = ? WHERE id = 1", (goal,))
        conn.commit()
        flash("Yıllık hedef güncellendi.", "success")
    goal = conn.execute("SELECT yearly_goal FROM settings WHERE id = 1").fetchone()["yearly_goal"]
    conn.close()
    return render_template("settings.html", goal=goal)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5010, debug=True)
