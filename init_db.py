from database import init_db

if __name__ == '__main__':
    print("Initializing PostgreSQL database...")
    try:
        init_db()
        print("Database initialized successfully.")
    except Exception as e:
        print(f"Error initializing database: {e}")
