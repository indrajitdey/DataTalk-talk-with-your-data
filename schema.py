import pymysql
import json
import os
from dotenv import load_dotenv



load_dotenv()
user = os.getenv("SQL_USER")
password = os.getenv("SQL_PASSWORD")
host = os.getenv("SQL_HOST")
api_key = os.getenv("API_KEY")

def get_connection():
    return pymysql.connect(
        host=host,
        user=user,
        password=password,
        autocommit=True
    )

def fetch_databases(cursor):
    excluded = {'information_schema', 'performance_schema', 'mysql', 'sys'}
    cursor.execute("SHOW DATABASES")
    return [db[0] for db in cursor.fetchall() if db[0] not in excluded]

def fetch_schema(cursor, db):
    schema = {"database": db, "tables": [], "views": []}
    cursor.execute(f"USE `{db}`")
    cursor.execute("""
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = %s
    """, (db,))
    
    for table_name, table_type in cursor.fetchall():
        try:
            cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
            columns = cursor.fetchall()
        except Exception as e:
            print(f"⚠️ Skipping {table_type.lower()} `{table_name}` due to error: {e}")
            continue

        column_data = [{
            "column_name": col[0],
            "type": col[1],
            "nullable": col[2] == "YES",
            "key": col[3],
            "default": col[4],
            "extra": col[5]
        } for col in columns]

        item = {
            "name": table_name,
            "table_type": table_type,
            "columns": column_data
        }

        if table_type == "VIEW":
            schema["views"].append(item)
        else:
            schema["tables"].append(item)

    return schema

def main():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        databases = fetch_databases(cursor)

        all_schemas = []
        for db in databases:
            print(f"🔍 Loading schema for: {db}")
            schema = fetch_schema(cursor, db)
            all_schemas.append(schema)

        with open("all_databases_schema.json", "w") as f:
            json.dump(all_schemas, f, indent=2)

        print("✅ All schemas saved to all_databases_schema.json")

    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()
