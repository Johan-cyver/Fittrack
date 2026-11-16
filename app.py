import os
import re
import threading
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
import mysql.connector
from mysql.connector import pooling
from flask import Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash\nfrom dotenv import load_dotenv\nfrom werkzeug.exceptions import HTTPException\n\nload_dotenv()

app = Flask(__name__, static_folder="public", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET and os.getenv("VERCEL"):
    raise RuntimeError("JWT_SECRET must be configured in Vercel")
JWT_SECRET = JWT_SECRET or "local-only-change-this-secret"

_mysql_pool = None
_mongo_client = None
_schema_lock = threading.Lock()
_schema_ready = False

SCHEMA = [
"""CREATE TABLE IF NOT EXISTS members (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(120) NOT NULL,
 email VARCHAR(190) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL,
 phone VARCHAR(30), date_of_birth DATE, role ENUM('admin','trainer','member') NOT NULL DEFAULT 'member',
 status ENUM('active','inactive','suspended') NOT NULL DEFAULT 'active',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB""",
"""CREATE TABLE IF NOT EXISTS trainers (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(120) NOT NULL,
 email VARCHAR(190) NOT NULL UNIQUE, phone VARCHAR(30), specialty VARCHAR(120),
 hire_date DATE, status ENUM('active','inactive') NOT NULL DEFAULT 'active',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB""",
"""CREATE TABLE IF NOT EXISTS membership_plans (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE,
 duration_days INT NOT NULL, price DECIMAL(10,2) NOT NULL,
 description TEXT, status ENUM('active','inactive') NOT NULL DEFAULT 'active',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 CHECK (duration_days > 0), CHECK (price >= 0)
) ENGINE=InnoDB""",
"""CREATE TABLE IF NOT EXISTS memberships (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, member_id BIGINT NOT NULL,
 plan_id BIGINT NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL,
 status ENUM('active','expired','cancelled','pending') NOT NULL DEFAULT 'pending',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (member_id) REFERENCES members(id),
 FOREIGN KEY (plan_id) REFERENCES membership_plans(id),
 INDEX idx_memberships_member (member_id), INDEX idx_memberships_status_end (status,end_date),
 CHECK (end_date >= start_date)
) ENGINE=InnoDB""",
"""CREATE TABLE IF NOT EXISTS payments (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, member_id BIGINT NOT NULL,
 membership_id BIGINT, amount DECIMAL(10,2) NOT NULL,
 payment_method VARCHAR(40) NOT NULL, reference_code VARCHAR(120) UNIQUE,
 status ENUM('pending','paid','failed','refunded') NOT NULL DEFAULT 'pending',
 paid_at DATETIME, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (member_id) REFERENCES members(id),
 FOREIGN KEY (membership_id) REFERENCES memberships(id),
 INDEX idx_payments_member (member_id), CHECK (amount >= 0)
) ENGINE=InnoDB""",
"""CREATE TABLE IF NOT EXISTS fitness_classes (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, title VARCHAR(120) NOT NULL,
 description TEXT, trainer_id BIGINT, starts_at DATETIME NOT NULL,
 capacity INT NOT NULL DEFAULT 20, status ENUM('scheduled','cancelled','completed') NOT NULL DEFAULT 'scheduled',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (trainer_id) REFERENCES trainers(id), CHECK (capacity > 0),
 INDEX idx_classes_starts (starts_at)
) ENGINE=InnoDB""",
"""CREATE TABLE IF NOT EXISTS class_assignments (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, class_id BIGINT NOT NULL,
 member_id BIGINT NOT NULL, status ENUM('booked','attended','cancelled') NOT NULL DEFAULT 'booked',
 booked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (class_id) REFERENCES fitness_classes(id) ON DELETE CASCADE,
 FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
 UNIQUE KEY uq_class_member (class_id,member_id), INDEX idx_assignments_member (member_id)
) ENGINE=InnoDB"""
]

ENTITY_CONFIG = {
 "members": ("members", ["name","email","phone","date_of_birth","role","status"], ["name","email","role","status"]),
 "trainers": ("trainers", ["name","email","phone","specialty","hire_date","status"], ["name","email"]),
 "plans": ("membership_plans", ["name","duration_days","price","description","status"], ["name","duration_days","price"]),
 "memberships": ("memberships", ["member_id","plan_id","start_date","end_date","status"], ["member_id","plan_id","start_date","end_date"]),
 "payments": ("payments", ["member_id","membership_id","amount","payment_method","reference_code","status","paid_at"], ["member_id","amount","payment_method"]),
 "classes": ("fitness_classes", ["title","description","trainer_id","starts_at","capacity","status"], ["title","starts_at"]),
 "assignments": ("class_assignments", ["class_id","member_id","status"], ["class_id","member_id"])
}
DOC_COLLECTIONS = {"workouts":"workout_logs","progress":"progress_logs","routines":"routines","activities":"activity_logs","feedback":"feedback"}

def mysql_pool():
    global _mysql_pool
    if _mysql_pool is None:
        required = ["MYSQL_HOST","MYSQL_USER","MYSQL_PASSWORD","MYSQL_DATABASE"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise RuntimeError("Missing MySQL configuration: " + ", ".join(missing))
        _mysql_pool = pooling.MySQLConnectionPool(
            pool_name="fittrack_pool", pool_size=5,
            host=os.environ["MYSQL_HOST"], port=int(os.getenv("MYSQL_PORT","3306")),
            user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
            database=os.environ["MYSQL_DATABASE"],
            connection_timeout=8, autocommit=False,
            ssl_disabled=os.getenv("MYSQL_SSL_DISABLED","false").lower() == "true"
        )
    return _mysql_pool

def get_db():
    return mysql_pool().get_connection()

def ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        conn = get_db()
        try:
            cur = conn.cursor()
            for statement in SCHEMA:
                cur.execute(statement)
            conn.commit()
            cur.close()
            _schema_ready = True
        finally:
            conn.close()

def mongo_db():
    global _mongo_client
    uri = os.getenv("MONGO_URI")
    if not uri:
        raise RuntimeError("MONGO_URI is not configured")
    if _mongo_client is None:
        _mongo_client = MongoClient(uri, serverSelectionTimeoutMS=8000, connectTimeoutMS=8000)
    db_name = os.getenv("MONGO_DATABASE")
    if db_name:
        return _mongo_client[db_name]
    return _mongo_client.get_default_database()

def json_error(message, status=400):
    return jsonify({"error": message}), status

def token_for(member):
    return jwt.encode({"sub": str(member["id"]), "role": member["role"],
                       "exp": datetime.now(timezone.utc) + timedelta(days=7)}, JWT_SECRET, algorithm="HS256")

def auth_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return json_error("Authentication required", 401)
        try:
            payload = jwt.decode(header[7:], JWT_SECRET, algorithms=["HS256"])
            request.member_id = int(payload["sub"])
            request.member_role = payload["role"]
        except (jwt.InvalidTokenError, ValueError, KeyError):
            return json_error("Invalid or expired session", 401)
        return fn(*args, **kwargs)
    return wrapped

def admin_required(fn):
    @wraps(fn)
    @auth_required
    def wrapped(*args, **kwargs):
        if request.member_role != "admin":
            return json_error("Administrator access required", 403)
        return fn(*args, **kwargs)
    return wrapped

def body_json():
    return request.get_json(silent=True) or {}

def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False

@app.get("/")
def home():
    return app.send_static_file("index.html")

@app.get("/api/health")
def health():
    checks = {}
    try:
        ensure_schema()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        conn.close()
        checks["mysql"] = "ok"
    except Exception as exc:
        checks["mysql"] = "error"
        checks["mysql_detail"] = str(exc)[:180]
    try:
        mongo_db().command("ping")
        checks["mongodb"] = "ok"
    except Exception as exc:
        checks["mongodb"] = "error"
        checks["mongodb_detail"] = str(exc)[:180]
    ok = all(checks.get(k) == "ok" for k in ("mysql","mongodb"))
    return jsonify({"status":"ok" if ok else "degraded","checks":checks}), 200 if ok else 503

@app.post("/api/auth/register")
def register():
    data = body_json()
    name, email, password = [str(data.get(k,"")).strip() for k in ("name","email","password")]
    if len(name) < 2 or len(name) > 120 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return json_error("Enter a valid name and email")
    if len(password) < 8 or len(password) > 128:
        return json_error("Password must be 8–128 characters")
    role = "admin" if email.lower() == os.getenv("ADMIN_EMAIL","").lower() and os.getenv("ADMIN_EMAIL") else "member"
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO members (name,email,password_hash,role) VALUES (%s,%s,%s,%s)",
                    (name,email.lower(),generate_password_hash(password),role))
        conn.commit()
        member_id = cur.lastrowid
        cur.execute("SELECT id,name,email,role,status FROM members WHERE id=%s",(member_id,))
        member = cur.fetchone()
        cur.close()
        return jsonify({"token":token_for(member),"member":member}),201
    except mysql.connector.IntegrityError:
        conn.rollback()
        return json_error("An account with this email already exists",409)
    finally:
        conn.close()

@app.post("/api/auth/login")
def login():
    data = body_json()
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id,name,email,password_hash,role,status FROM members WHERE email=%s",(str(data.get("email","")).strip().lower(),))
        member = cur.fetchone()
        cur.close()
        if not member or member["status"] != "active" or not check_password_hash(member["password_hash"],str(data.get("password",""))):
            return json_error("Invalid email or password",401)
        member.pop("password_hash",None)
        return jsonify({"token":token_for(member),"member":member})
    finally:
        conn.close()

@app.get("/api/auth/me")
@auth_required
def me():
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id,name,email,phone,role,status,created_at FROM members WHERE id=%s",(request.member_id,))
        member = cur.fetchone()
        cur.close()
        return (jsonify(member),200) if member else json_error("Account not found",404)
    finally:
        conn.close()

@app.route("/api/admin/<entity>", methods=["GET","POST"])
@admin_required
def admin_collection(entity):
    if entity not in ENTITY_CONFIG:
        return json_error("Unknown data entity",404)
    table, allowed, required = ENTITY_CONFIG[entity]
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        if request.method == "GET":
            cur.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 200")
            rows = cur.fetchall()
            cur.close()
            return jsonify(rows)
        data = body_json()
        missing = [k for k in required if data.get(k) in (None,"")]
        if missing:
            return json_error("Required fields: " + ", ".join(missing))
        fields = [k for k in allowed if k in data]
        if not fields:
            return json_error("No valid fields supplied")
        sql = f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join(['%s']*len(fields))})"
        cur.execute(sql,tuple(data[k] for k in fields))
        conn.commit()
        new_id = cur.lastrowid
        cur.execute(f"SELECT * FROM {table} WHERE id=%s",(new_id,))
        row = cur.fetchone()
        cur.close()
        return jsonify(row),201
    except mysql.connector.Error as exc:
        conn.rollback()
        return json_error("Database constraint rejected the operation: " + str(exc.msg),409)
    finally:
        conn.close()

@app.route("/api/admin/<entity>/<int:row_id>", methods=["PATCH","DELETE"])
@admin_required
def admin_record(entity,row_id):
    if entity not in ENTITY_CONFIG:
        return json_error("Unknown data entity",404)
    table, allowed, required = ENTITY_CONFIG[entity]
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        if request.method == "DELETE":
            cur.execute(f"DELETE FROM {table} WHERE id=%s",(row_id,))
            conn.commit()
            changed = cur.rowcount
            cur.close()
            return (jsonify({"deleted":True}),200) if changed else json_error("Record not found",404)
        data = body_json()
        fields = [k for k in allowed if k in data]
        if not fields:
            return json_error("No valid fields supplied")
        cur.execute(f"UPDATE {table} SET "+",".join(f"{k}=%s" for k in fields)+" WHERE id=%s",
                    tuple(data[k] for k in fields)+(row_id,))
        conn.commit()
        cur.execute(f"SELECT * FROM {table} WHERE id=%s",(row_id,))
        row = cur.fetchone()
        cur.close()
        return (jsonify(row),200) if row else json_error("Record not found",404)
    except mysql.connector.Error as exc:
        conn.rollback()
        return json_error("Database constraint rejected the operation: " + str(exc.msg),409)
    finally:
        conn.close()

@app.route("/api/logs/<kind>", methods=["GET","POST"])
@auth_required
def document_logs(kind):
    if kind not in DOC_COLLECTIONS:
        return json_error("Unknown document collection",404)
    collection = mongo_db()[DOC_COLLECTIONS[kind]]
    if request.method == "POST":
        data = body_json()
        data.pop("_id",None)
        data["member_id"] = request.member_id
        data["created_at"] = datetime.now(timezone.utc)
        if kind == "progress" and (not isinstance(data.get("weight_kg"),(int,float)) or data["weight_kg"] <= 0):
            return json_error("weight_kg must be a positive number")
        if kind == "workouts" and not data.get("activity"):
            return json_error("activity is required")
        result = collection.insert_one(data)
        return jsonify({"id":str(result.inserted_id),"saved":True}),201
    docs = list(collection.find({"member_id":request.member_id}).sort("created_at",-1).limit(100))
    for doc in docs:
        doc["_id"] = str(doc["_id"])
    return jsonify(docs)

@app.delete("/api/logs/<kind>/<doc_id>")
@auth_required
def delete_document_log(kind,doc_id):
    if kind not in DOC_COLLECTIONS:
        return json_error("Unknown document collection",404)
    from bson import ObjectId
    if not ObjectId.is_valid(doc_id):
        return json_error("Invalid document id")
    result = mongo_db()[DOC_COLLECTIONS[kind]].delete_one({"_id":ObjectId(doc_id),"member_id":request.member_id})
    return (jsonify({"deleted":True}),200) if result.deleted_count else json_error("Record not found",404)

@app.get("/api/dashboard")
@auth_required
def dashboard():
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS members FROM members WHERE role='member' AND status='active'")
        members = cur.fetchone()["members"]
        cur.execute("SELECT COUNT(*) AS trainers FROM trainers WHERE status='active'")
        trainers = cur.fetchone()["trainers"]
        cur.execute("SELECT COUNT(*) AS active_memberships FROM memberships WHERE status='active' AND end_date >= CURDATE()")
        memberships = cur.fetchone()["active_memberships"]
        cur.execute("SELECT COUNT(*) AS upcoming_classes FROM fitness_classes WHERE status='scheduled' AND starts_at >= NOW()")
        classes = cur.fetchone()["upcoming_classes"]
        cur.close()
    finally:
        conn.close()
    db = mongo_db()
    return jsonify({"members":members,"trainers":trainers,"active_memberships":memberships,
                    "upcoming_classes":classes,"my_workouts":db.workout_logs.count_documents({"member_id":request.member_id}),
                    "my_progress_entries":db.progress_logs.count_documents({"member_id":request.member_id})})

@app.errorhandler(413)
def too_large(_):
    return json_error("Request body exceeds 1 MB",413)

@app.errorhandler(404)
def not_found(_):
    if request.path.startswith("/api/"):
        return json_error("API route not found",404)
    return app.send_static_file("index.html")

@app.errorhandler(Exception)
def unexpected_error(exc):
    app.logger.exception("Unhandled request error")
    return json_error("Internal server error",500)

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","5000")),debug=False)
