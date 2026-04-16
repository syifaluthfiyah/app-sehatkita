from flask import Flask, render_template, request
import pymysql
import os
import uuid
import boto3
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from botocore.exceptions import BotoCoreError, ClientError

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))


def get_s3_client():
    session_kwargs = {}

    aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY")
    aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_KEY")
    aws_session_token = os.getenv("AWS_SESSION_TOKEN")
    aws_region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")

    if aws_access_key_id and aws_secret_access_key:
        session_kwargs["aws_access_key_id"] = aws_access_key_id
        session_kwargs["aws_secret_access_key"] = aws_secret_access_key
        if aws_session_token:
            session_kwargs["aws_session_token"] = aws_session_token

    if aws_region:
        session_kwargs["region_name"] = aws_region

    session = boto3.Session(**session_kwargs)
    return session.client("s3")


def get_s3_bucket_name():
    return os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_BUCKET", "")


def get_s3_prefix():
    return os.getenv("S3_PREFIX", "uploads").strip("/")


def build_s3_object_key(filename):
    prefix = get_s3_prefix()
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    if prefix:
        return f"{prefix}/{unique_name}"
    return unique_name


def create_presigned_url(object_key):
    bucket_name = get_s3_bucket_name()
    if not bucket_name or not object_key:
        return None

    try:
        s3_client = get_s3_client()
        return s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket_name, "Key": object_key},
            ExpiresIn=int(os.getenv("S3_PRESIGNED_EXPIRES", "3600"))
        )
    except (BotoCoreError, ClientError, ValueError):
        return None

# ========================
# DATABASE
# ========================
def get_db_connection():
    connection_kwargs = {
        "host": os.getenv("DB_HOST", "localhost"),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "sehat_db"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "charset": "utf8mb4",
        "connect_timeout": 10,
        "read_timeout": 10,
        "write_timeout": 10,
    }

    db_ssl_ca = os.getenv("DB_SSL_CA")
    if db_ssl_ca:
        connection_kwargs["ssl"] = {"ca": db_ssl_ca}

    return pymysql.connect(**connection_kwargs)

# ========================
# HOME
# ========================
@app.route("/")
def index():
    return render_template("index.html")


# ========================
# BOOKING
# ========================
@app.route("/booking", methods=["GET", "POST"])
def booking():
    pesan = None
    data = []
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        if request.method == "POST":
            nama = request.form.get("nama")
            tanggal = request.form.get("tanggal")

            if not nama or not tanggal:
                pesan = "Data tidak boleh kosong!"
            else:
                cursor.execute(
                    "INSERT INTO booking (nama, tanggal) VALUES (%s, %s)",
                    (nama, tanggal)
                )
                connection.commit()
                pesan = "Booking berhasil!"

        cursor.execute("SELECT * FROM booking ORDER BY id DESC")
        data = cursor.fetchall()
    except pymysql.MySQLError:
        pesan = "Koneksi database gagal"
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return render_template("booking.html", bookings=data, pesan=pesan)


# ========================
# UPLOAD FILE
# ========================
@app.route("/upload", methods=["GET", "POST"])
def upload():
    pesan = None
    nama_file = None
    riwayat = []
    connection = None
    cursor = None
    bucket_name = get_s3_bucket_name()

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        if request.method == "POST":

            file = request.files.get("file")

            if not file or file.filename == "":
                pesan = "File belum dipilih!"
            else:
                original_filename = secure_filename(file.filename)
                if not original_filename:
                    pesan = "Nama file tidak valid!"
                    return render_template(
                        "upload.html",
                        pesan=pesan,
                        nama_file=nama_file,
                        riwayat=riwayat
                    )

                if not bucket_name:
                    pesan = "S3_BUCKET_NAME belum diatur"
                    return render_template(
                        "upload.html",
                        pesan=pesan,
                        nama_file=nama_file,
                        riwayat=riwayat
                    )

                s3_key = build_s3_object_key(original_filename)
                s3_client = get_s3_client()

                s3_client.upload_fileobj(
                    file,
                    bucket_name,
                    s3_key,
                    ExtraArgs={
                        "ContentType": file.content_type or "application/octet-stream"
                    }
                )

                cursor.execute(
                    "INSERT INTO upload (nama_file, path_file) VALUES (%s, %s)",
                    (original_filename, s3_key)
                )
                connection.commit()

                pesan = "Upload berhasil!"
                nama_file = original_filename

        cursor.execute("SELECT * FROM upload ORDER BY id DESC")
        riwayat = [
            {
                "id": row[0],
                "nama_file": row[1],
                "path_file": row[2],
                "file_url": create_presigned_url(row[2])
            }
            for row in cursor.fetchall()
        ]
    except pymysql.MySQLError:
        pesan = "Koneksi database gagal"
    except (BotoCoreError, ClientError):
        pesan = "Upload ke S3 gagal"
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return render_template(
        "upload.html",
        pesan=pesan,
        nama_file=nama_file,
        riwayat=riwayat
    )


# ========================
# FILE LIST (OPSIONAL)
# ========================
@app.route("/files")
def files():
    data = []
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM upload ORDER BY id DESC")
        data = [
            {
                "id": row[0],
                "nama_file": row[1],
                "path_file": row[2],
                "file_url": create_presigned_url(row[2])
            }
            for row in cursor.fetchall()
        ]
    except pymysql.MySQLError:
        data = []
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
    return render_template("files.html", files=data)


# ========================
# MONITORING (DINONAKTIFKAN / OPTIONAL)
# ========================
# Kalau tidak dipakai, lebih baik dihapus saja
# supaya tidak bingung dan tidak error template

# @app.route("/monitoring")
# def monitoring():
#     cursor = db.cursor()
#     cursor.execute("SELECT * FROM monitoring_penyakit ORDER BY tanggal DESC")
#     data = cursor.fetchall()
#     return render_template("monitoring.html", data=data)


# ========================
# RUN SERVER
# ========================
if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true"
    )