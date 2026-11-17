import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
import mysql.connector
from mysql.connector import pooling
from flask import Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException

load_dotenv()

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
_mongo_indexes_ready = False

@app.before_request
def initialize_api_schema():
    if request.path.startswith("/api/") and request.path != "/api/health":
        ensure_schema()

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
) ENGINE=InnoDB""",
"""CREATE TABLE IF NOT EXISTS fittrack_seed_state (
 seed_key VARCHAR(80) PRIMARY KEY, seeded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        db_name = os.environ["MYSQL_DATABASE"]
        if not re.fullmatch(r"[A-Za-z0-9_]+", db_name):
            raise RuntimeError("MYSQL_DATABASE contains unsupported characters")
        connection_options = {
            "host": os.environ["MYSQL_HOST"],
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.environ["MYSQL_USER"],
            "password": os.environ["MYSQL_PASSWORD"],
            "connection_timeout": 8,
            "ssl_disabled": os.getenv("MYSQL_SSL_DISABLED", "false").lower() == "true"
        }
        # TiDB/MySQL Starter may begin without an application database.
        # Bootstrap it using the supplied account, then create the pool.
        bootstrap = mysql.connector.connect(**connection_options)
        try:
            cursor = bootstrap.cursor()
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}`")
            cursor.close()
        finally:
            bootstrap.close()
        _mysql_pool = pooling.MySQLConnectionPool(
            pool_name="fittrack_pool", pool_size=5,
            **connection_options, database=db_name, autocommit=False
        )
    return _mysql_pool

def get_db():
    return mysql_pool().get_connection()

def seed_demo_data(conn):
    """Insert a small, clearly synthetic gym dataset once when explicitly enabled."""
    if os.getenv("FITTRACK_SEED_DEMO", "false").lower() != "true":
        return
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT seed_key FROM fittrack_seed_state WHERE seed_key='fittrack-demo-v1'")
    if cur.fetchone():
        cur.close()
        return

    trainers = [
        ("Aarav Menon","aarav.trainer@example.test","Strength & Conditioning"),
        ("Maya Iyer","maya.trainer@example.test","Yoga & Mobility"),
        ("Rohan Das","rohan.trainer@example.test","HIIT & Functional Fitness")
    ]
    for name,email,specialty in trainers:
        cur.execute("INSERT IGNORE INTO trainers (name,email,specialty,hire_date,status) VALUES (%s,%s,%s,CURDATE(),'active')",
                    (name,email,specialty))

    plans = [
        ("Starter",30,999.00,"30-day access to the gym floor and essential facilities."),
        ("Performance",90,2499.00,"90-day access with group classes and progress reviews."),
        ("Elite",365,8999.00,"Annual access with priority class booking and coaching check-ins.")
    ]
    for name,duration,price,description in plans:
        cur.execute("INSERT IGNORE INTO membership_plans (name,duration_days,price,description,status) VALUES (%s,%s,%s,%s,'active')",
                    (name,duration,price,description))

    demo_members = [
        ("Nikhil Rao","nikhil.rao@example.test"),
        ("Ananya Shah","ananya.shah@example.test"),
        ("Kabir Nair","kabir.nair@example.test"),
        ("Diya Thomas","diya.thomas@example.test"),
        ("Ishaan Kulkarni","ishaan.kulkarni@example.test")
    ]
    member_ids = []
    for name,email in demo_members:
        # Random unusable demo passwords: these records are sample data, not shared login accounts.
        cur.execute("INSERT IGNORE INTO members (name,email,password_hash,role,status) VALUES (%s,%s,%s,'member','active')",
                    (name,email,generate_password_hash(secrets.token_urlsafe(32))))
        cur.execute("SELECT id FROM members WHERE email=%s",(email,))
        member_ids.append(cur.fetchone()["id"])

    trainer_ids = {}
    for _,email,_ in trainers:
        cur.execute("SELECT id FROM trainers WHERE email=%s",(email,))
        trainer_ids[email] = cur.fetchone()["id"]
    plan_ids = {}
    for name,_,_,_ in plans:
        cur.execute("SELECT id FROM membership_plans WHERE name=%s",(name,))
        plan_ids[name] = cur.fetchone()["id"]

    today = datetime.now().date()
    for i,member_id in enumerate(member_ids):
        plan_name = plans[i % len(plans)][0]
        start = today - timedelta(days=7 * (i + 1))
        end = start + timedelta(days=plans[i % len(plans)][1])
        cur.execute("INSERT INTO memberships (member_id,plan_id,start_date,end_date,status) VALUES (%s,%s,%s,%s,'active')",
                    (member_id,plan_ids[plan_name],start,end))
        membership_id = cur.lastrowid
        cur.execute("INSERT INTO payments (member_id,membership_id,amount,payment_method,reference_code,status,paid_at) VALUES (%s,%s,%s,'demo','FITTRACK-DEMO-%s','paid',NOW())",
                    (member_id,membership_id,plans[i % len(plans)][2],member_id))

    class_specs = [
        ("Strength Foundations","Build safe, repeatable strength with coach-led fundamentals.",trainer_ids[trainers[0][1]],1,18),
        ("Sunrise Yoga","Mobility, balance and a calmer start to the day.",trainer_ids[trainers[1][1]],2,20),
        ("HIIT Circuit","Short, high-energy intervals with scalable movements.",trainer_ids[trainers[2][1]],3,16),
        ("Full Body Conditioning","A coached full-body session for all experience levels.",trainer_ids[trainers[0][1]],4,18),
        ("Mobility Reset","Stretching and mobility for recovery days.",trainer_ids[trainers[1][1]],5,20),
        ("Functional Fitness","Strength, stamina and movement in one session.",trainer_ids[trainers[2][1]],6,16)
    ]
    class_ids = []
    for title,description,trainer_id,day_offset,capacity in class_specs:
        starts = datetime.now().replace(hour=7 + (day_offset % 3) * 4,minute=0,second=0,microsecond=0) + timedelta(days=day_offset)
        cur.execute("INSERT INTO fitness_classes (title,description,trainer_id,starts_at,capacity,status) VALUES (%s,%s,%s,%s,%s,'scheduled')",
                    (title,description,trainer_id,starts,capacity))
        class_ids.append(cur.lastrowid)

    for i,member_id in enumerate(member_ids):
        cur.execute("INSERT IGNORE INTO class_assignments (class_id,member_id,status) VALUES (%s,%s,'booked')",
                    (class_ids[i % len(class_ids)],member_id))
    cur.execute("INSERT INTO fittrack_seed_state (seed_key) VALUES ('fittrack-demo-v1')")
    cur.close()


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
            seed_demo_data(conn)
            conn.commit()
            _schema_ready = True
        finally:
            conn.close()

def mongo_db():
    global _mongo_client, _mongo_indexes_ready
    uri = os.getenv("MONGO_URI")
    if not uri:
        raise RuntimeError("MONGO_URI is not configured")
    if _mongo_client is None:
        _mongo_client = MongoClient(uri, serverSelectionTimeoutMS=8000, connectTimeoutMS=8000)
    db_name = os.getenv("MONGO_DATABASE")
    database = _mongo_client[db_name] if db_name else _mongo_client.get_default_database()
    if not _mongo_indexes_ready:
        for collection_name in DOC_COLLECTIONS.values():
            database[collection_name].create_index([("member_id", 1), ("created_at", -1)])
        _mongo_indexes_ready = True
    if os.getenv("FITTRACK_SEED_DEMO", "false").lower() == "true":
        seed_demo_mongo(database)
    return database


def seed_demo_mongo(database):
    """Add synthetic activity documents for the SQL demo members, once per record."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id,email FROM members WHERE email LIKE %s ORDER BY id",
                    ("%@example.test",))
        members = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    now = datetime.now(timezone.utc)
    workout_samples = [
        ("Strength training",45,320,"Upper-body strength session"),
        ("Treadmill run",30,260,"Steady cardio session"),
        ("Functional circuit",40,350,"Full-body conditioning")
    ]
    for index, member in enumerate(members):
        member_id = member["id"]
        workouts = database["workout_logs"]
        for offset,(activity,duration,calories,notes) in enumerate(workout_samples):
            key = f"fittrack-demo-workout-{member_id}-{offset+1}"
            workouts.update_one({"demo_seed_key":key},{"$setOnInsert":{
                "demo_seed_key":key,"member_id":member_id,"activity":activity,
                "duration_min":duration,"calories":calories,"notes":notes,
                "created_at":now-timedelta(days=offset+index)
            }},upsert=True)

        progress = database["progress_logs"]
        for offset,weight in enumerate((78.5-index*1.2,78.0-index*1.2)):
            key = f"fittrack-demo-progress-{member_id}-{offset+1}"
            progress.update_one({"demo_seed_key":key},{"$setOnInsert":{
                "demo_seed_key":key,"member_id":member_id,"weight_kg":weight,
                "notes":"Synthetic demonstration measurement",
                "created_at":now-timedelta(days=14-offset*7)
            }},upsert=True)

        routine_key = f"fittrack-demo-routine-{member_id}"
        database["routines"].update_one({"demo_seed_key":routine_key},{"$setOnInsert":{
            "demo_seed_key":routine_key,"member_id":member_id,
            "name":"Balanced strength starter","goal":"Build strength and consistency",
            "days_per_week":4,"created_at":now
        }},upsert=True)

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
            columns = "id,name,email,phone,date_of_birth,role,status,created_at,updated_at" if entity == "members" else "*"
            cur.execute(f"SELECT {columns} FROM {table} ORDER BY id DESC LIMIT 200")
            rows = cur.fetchall()
            cur.close()
            return jsonify(rows)
        data = body_json()
        if entity == "members":
            return json_error("Create member accounts through /api/auth/register so passwords are securely hashed")
        if entity == "assignments":
            try:
                class_id = int(data.get("class_id"))
            except (TypeError, ValueError):
                return json_error("A valid class_id is required")
            cur.execute("SELECT capacity,status FROM fitness_classes WHERE id=%s FOR UPDATE", (class_id,))
            class_row = cur.fetchone()
            if not class_row or class_row["status"] != "scheduled":
                conn.rollback()
                return json_error("Class not found or not open for booking", 409)
            cur.execute("SELECT COUNT(*) AS booked FROM class_assignments WHERE class_id=%s AND status <> 'cancelled'", (class_id,))
            if cur.fetchone()["booked"] >= class_row["capacity"]:
                conn.rollback()
                return json_error("Class is full", 409)
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
        if entity == "members" and row:
            row.pop("password_hash", None)
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
        if entity == "members" and row:
            row.pop("password_hash", None)
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
    if isinstance(exc, HTTPException):
        return json_error(exc.description, exc.code or 400)
    app.logger.exception("Unhandled request error")
    return json_error("Internal server error",500)

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","5000")),debug=False)
