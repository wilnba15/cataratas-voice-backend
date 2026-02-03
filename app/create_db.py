from app.db import engine, Base
import app.models  # fuerza a cargar los modelos

print("Creando base de datos...")

Base.metadata.create_all(bind=engine)

print("✅ Base de datos creada correctamente")
