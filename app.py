from flask import Flask, request, jsonify, make_response, render_template
from flask_cors import CORS
import json, requests, pymysql, os, re
from decimal import Decimal
import sqlparse
import certifi
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv()

user = os.getenv("SQL_USER")
password = os.getenv("SQL_PASSWORD")
host = os.getenv("SQL_HOST")
api_key = os.getenv("API_KEY")
GROQ_API_KEY = api_key

if not all([user, password, host, api_key]):
    raise EnvironmentError("Missing one or more required environment variables.")

DB_CONFIG = {
    "host": host,
    "user": user,
    "password": password
}

os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
app = Flask(__name__)
CORS(app)

# --- LOAD MULTIPLE DATABASE SCHEMAS ---
with open("all_databases_schema.json", "r") as f:
    raw_schemas = json.load(f)

schemas = {
    db_schema["database"]: {
        **{
            table["name"]: {
                "columns": {col["column_name"]: col["type"] for col in table["columns"]}
            } for table in db_schema.get("tables", [])
        },
        **{
            view["name"]: {
                "columns": {col["column_name"]: col["type"] for col in view["columns"]}
            } for view in db_schema.get("views", [])
        }
    } for db_schema in raw_schemas
}

# --- GENERATE SQL ---
def generate_sql(prompt, db_schema, db_name):
    schema_info = "\n".join(
        f"{table}: {', '.join([f'{col} ({dtype})' for col, dtype in db_schema[table]['columns'].items()])}"
        for table in db_schema
    )

    system_msg = (
        f"You are an expert SQL assistant. Use the following schema from database `{db_name}`:\n{schema_info}\n"
        f"If the user asks about tables or views, include both table_name and table_type.\n"
        f"The user always ask you questions about data which is inside table(all the records) so always generate a quary for a table"
        f"Output valid SQL only. No explanations."
    )

    payload = {
        "model": "llama-3.3-70b-versatile",
        "temperature": 0,
        "max_tokens": 200,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt.strip()}
        ]
    }

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers)

    if res.status_code == 200:
        sql_text = res.json()["choices"][0]["message"]["content"]

        sql_text = re.sub(r"```sql\s*", "", sql_text, flags=re.IGNORECASE)
        sql_text = re.sub(r"\s*```$", "", sql_text)

        lines = sql_text.strip().splitlines()
        for i, line in enumerate(lines):
            if line.strip().lower().startswith(("select", "show", "with", "describe", "explain")):
                sql_text = "\n".join(lines[i:])
                break

        sql_text = sql_text.replace("'your_database_name'", f"'{db_name}'")

        if re.search(r"(tables|views)", prompt, re.IGNORECASE) and "table_type" not in sql_text.lower():
            if "FROM information_schema.tables" in sql_text:
                sql_text = sql_text.replace("SELECT table_name", "SELECT table_name, table_type")

        try:
            return str(sqlparse.parse(sql_text.strip())[0])
        except Exception as parse_err:
            raise ValueError(f"SQL parsing error: {parse_err}")
    else:
        raise Exception(f"GROQ error: {res.status_code} - {res.text}")

# --- DATABASE QUERY ---
def query_mysql(sql, db_name):
    config = DB_CONFIG.copy()
    config["database"] = db_name
    conn = pymysql.connect(**config)
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return rows, columns
    finally:
        cursor.close()
        conn.close()

# --- INSIGHT GENERATION ---
def generate_insight(rows, columns, prompt):
    formatted = [
        {columns[i]: (float(cell) if isinstance(cell, Decimal) else cell) for i, cell in enumerate(row)}
        for row in rows
    ]

    payload = {
        "model": "llama-3.3-70b-versatile",
        "temperature": 0.3,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": "You're a data analyst. Provide concise insights."},
            {"role": "user", "content": f"User question: {prompt}"},
            {"role": "user", "content": f"Data:\n{json.dumps(formatted, indent=2)}"}
        ]
    }

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers)

    if res.status_code == 200:
        return res.json()["choices"][0]["message"]["content"]
    else:
        raise Exception(f"GROQ Insight error: {res.status_code} - {res.text}")

# --- ROUTES ---
@app.route("/")
def index():
    db_names = list(schemas.keys())
    return render_template("index.html", databases=db_names)

@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        data = request.get_json(force=True)
        prompt = data.get("prompt")
        db_name = data.get("database")

        if not prompt or not db_name:
            return make_response(jsonify({"error": "Missing prompt or database"}), 400)

        if db_name not in schemas:
            return make_response(jsonify({"error": f"Unknown database: {db_name}"}), 400)

        db_schema = schemas[db_name]
        sql_query = generate_sql(prompt, db_schema, db_name)
        rows, columns = query_mysql(sql_query, db_name)
        insight = generate_insight(rows, columns, prompt) if rows else "No data found for this query."

        return jsonify({
            "sql": sql_query,
            "columns": columns,
            "rows": rows,
            "insight": insight
        })

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        return make_response(jsonify({"error": str(e)}), 500)

@app.route("/test")
def test():
    return jsonify({"status": "Flask JSON working ✅"})

if __name__ == "__main__":
    app.run(debug=True)
# for run : py app.py