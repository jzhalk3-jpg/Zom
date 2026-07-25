import sqlite3
from config import DB_NAME

def get_connection():
    return sqlite3.connect(DB_NAME, check_same_thread=False)

def init_db():
    db = get_connection()
    cursor = db.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, 
        session TEXT, 
        phone TEXT,
        is_active INTEGER DEFAULT 0
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, 
        delay INTEGER DEFAULT 10,
        rest_time INTEGER DEFAULT 5,
        balance INTEGER DEFAULT 0
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        user_id INTEGER, 
        link TEXT, 
        status TEXT DEFAULT 'pending'
    )
    """)
    
    db.commit()
    db.close()
