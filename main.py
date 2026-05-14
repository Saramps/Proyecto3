from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo import MongoClient
from pymongo import ASCENDING, DESCENDING
from bson import ObjectId
from datetime import datetime, timezone
import os

app = FastAPI(title="Dann-Alpes Reviews API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# Conexión a MongoDB (usar variable de entorno en Render)
client = MongoClient(os.environ["MONGO_URI"])
db = client["Proyecto3"]

resenas = db["reviews"]


# RF1 – Crear reseña

@app.post("/hoteles/{hotel_id}/resenas")
def crear_resena(hotel_id: str, datos: dict):
    cliente_id = datos.get("cliente_id")
    reserva_id = datos.get("reserva_id")
    calificacion = datos.get("calificacion")
    texto = datos.get("texto")

    if not all([cliente_id, reserva_id, calificacion, texto]):
        raise HTTPException(status_code=400, detail="Faltan campos obligatorios: cliente_id, reserva_id, calificacion, texto")

    if not (1 <= int(calificacion) <= 5):
        raise HTTPException(status_code=400, detail="La calificacion debe estar entre 1 y 5")

    # Verificar que no exista ya una reseña para esa reserva
    existente = resenas.find_one({"codigo_confirmacion": reserva_id, "estado": "publicada"})
    if existente:
        raise HTTPException(status_code=409, detail="Ya existe una reseña para esta reserva")

    nombre_hotel = datos.get("nombre_hotel", "")
    ciudad_hotel  = datos.get("ciudad_hotel", "")

    doc = {
        "id_hotel": int(hotel_id),           # entero, igual que en pipelines RFC
        "nombre_hotel": nombre_hotel,
        "ciudad_hotel": ciudad_hotel,
        "cedula_cliente": cliente_id,
        "codigo_confirmacion": reserva_id,
        "calificacion": int(calificacion),
        "texto": texto,
        "fecha_creacion": datetime.now(timezone.utc),  # datetime real para $month/$year
        "fecha_actualizacion": datetime.now(timezone.utc),
        "estado": "publicada",
        "destacada": False,
        "votos_utilidad": [],
        "total_votos": 0,
        "respuesta_admin": None
    }

    resultado = resenas.insert_one(doc)
    return {"mensaje": "Reseña creada exitosamente", "id": str(resultado.inserted_id)}


# RF2 – Editar reseña (cliente)

@app.put("/resenas/{resena_id}")
def editar_resena(resena_id: str, datos: dict):
    cliente_id = datos.get("cliente_id")
    calificacion = datos.get("calificacion")
    texto = datos.get("texto")

    if not cliente_id:
        raise HTTPException(status_code=400, detail="Se requiere cliente_id")

    resena = resenas.find_one({"_id": ObjectId(resena_id), "cedula_cliente": cliente_id, "estado": "publicada"})
    if not resena:
        raise HTTPException(status_code=404, detail="Reseña no encontrada o no pertenece al cliente")

    cambios = {"fecha_actualizacion": datetime.now(timezone.utc).isoformat()}
    if calificacion is not None:
        if not (1 <= int(calificacion) <= 5):
            raise HTTPException(status_code=400, detail="La calificacion debe estar entre 1 y 5")
        cambios["calificacion"] = int(calificacion)
    if texto is not None:
        cambios["texto"] = texto

    resenas.update_one({"_id": ObjectId(resena_id)}, {"$set": cambios})
    return {"mensaje": "Reseña actualizada exitosamente"}


# RF3 – Eliminar reseña (cliente)

@app.delete("/resenas/{resena_id}")
def eliminar_resena_cliente(resena_id: str, cliente_id: str = Query(...)):
    resena = resenas.find_one({"_id": ObjectId(resena_id), "cedula_cliente": cliente_id})
    if not resena:
        raise HTTPException(status_code=404, detail="Reseña no encontrada o no pertenece al cliente")

    resenas.update_one(
        {"_id": ObjectId(resena_id)},
        {"$set": {"estado": "eliminada", "fecha_actualizacion": datetime.now(timezone.utc).isoformat()}}
    )
    return {"mensaje": "Reseña eliminada exitosamente"}



# RF4 – Consultar reseñas de un hotel (público)

@app.get("/hoteles/{hotel_id}/resenas")
def get_resenas_hotel(
    hotel_id: str,
    orden: str = Query("fecha", pattern="^(fecha|utilidad)$"),
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(10, ge=1, le=50)
):
    filtro = {"id_hotel": int(hotel_id), "estado": "publicada"}
    sort_field = "fecha_creacion" if orden == "fecha" else "total_votos"

    # Reseña destacada va primero
    pipeline = [
        {"$match": filtro},
        {"$sort": {"destacada": DESCENDING, sort_field: DESCENDING}},
        {"$skip": (pagina - 1) * por_pagina},
        {"$limit": por_pagina},
        {"$project": {
            "_id": {"$toString": "$_id"},
            "cliente_id": 1,
            "calificacion": 1,
            "texto": 1,
            "fecha_creacion": 1,
            "votos_utilidad": 1,
            "destacada": 1,
            "respuesta_admin": 1
        }}
    ]

    docs = list(resenas.aggregate(pipeline))
    total = resenas.count_documents(filtro)
    return {"total": total, "pagina": pagina, "por_pagina": por_pagina, "resenas": docs}


# RF5 – Marcar reseña como útil
@app.post("/resenas/{resena_id}/util")
def marcar_util(resena_id: str, datos: dict):
    cliente_id = datos.get("cliente_id")
    if not cliente_id:
        raise HTTPException(status_code=400, detail="Se requiere cliente_id")

    resena = resenas.find_one({"_id": ObjectId(resena_id), "estado": "publicada"})
    if not resena:
        raise HTTPException(status_code=404, detail="Reseña no encontrada")

    if cliente_id in resena.get("votos_utilidad", []):
        raise HTTPException(status_code=409, detail="Ya votaste por esta reseña")

    resenas.update_one(
        {"_id": ObjectId(resena_id)},
        {
            "$inc": {"total_votos": 1},
            "$push": {"votos_utilidad": cliente_id}
        }
    )
    return {"mensaje": "Voto registrado exitosamente"}


# RF6 – Historial de reseñas propias


@app.get("/clientes/{cliente_id}/resenas")
def historial_resenas(
    cliente_id: str,
    orden: str = Query("fecha", pattern="^(fecha|hotel)$")
):
    sort_field = "fecha_creacion" if orden == "fecha" else "id_hotel"
    docs = list(resenas.find(
        {"cedula_cliente": cliente_id},
        {
            "_id": 1, "id_hotel": 1, "nombre_hotel": 1, "calificacion": 1, "texto": 1,
            "estado": 1, "fecha_creacion": 1, "total_votos": 1,
            "votos_utilidad": 1, "respuesta_admin": 1, "destacada": 1
        }
    ).sort(sort_field, DESCENDING))
    for d in docs:
        d["_id"] = str(d["_id"])
        d["tiene_respuesta"] = d.get("respuesta_admin") is not None
    return docs


# RF7 – Responder reseña (administrador)


@app.post("/responder/{resena_id}")
def responder_resena(resena_id: str, datos: dict):
    admin_id = datos.get("admin_id")
    texto_respuesta = datos.get("texto_respuesta")

    if not all([admin_id, texto_respuesta]):
        raise HTTPException(status_code=400, detail="Se requieren admin_id y texto_respuesta")

    resena = resenas.find_one({"_id": ObjectId(resena_id), "estado": "publicada"})
    if not resena:
        raise HTTPException(status_code=404, detail="Reseña no encontrada")

    respuesta = {
        "admin_id": admin_id,
        "texto": texto_respuesta,
        "fecha": datetime.now(timezone.utc).isoformat()
    }

    resenas.update_one(
        {"_id": ObjectId(resena_id)},
        {"$set": {"respuesta_admin": respuesta, "fecha_actualizacion": datetime.now(timezone.utc).isoformat()}}
    )
    return {"mensaje": "Respuesta registrada exitosamente"}


# RF8 – Eliminar reseña (administrador)


@app.delete("/eliminar/{resena_id}")
def eliminar_resena_admin(resena_id: str, admin_id: str = Query(...)):
    resena = resenas.find_one({"_id": ObjectId(resena_id)})
    if not resena:
        raise HTTPException(status_code=404, detail="Reseña no encontrada")

    resenas.update_one(
        {"_id": ObjectId(resena_id)},
        {"$set": {
            "estado": "eliminada_admin",
            "eliminada_por": admin_id,
            "fecha_actualizacion": datetime.now(timezone.utc).isoformat()
        }}
    )
    return {"mensaje": "Reseña eliminada por administrador"}


# RF9 – Destacar reseña (administrador)

@app.post("/destacar/{hotel_id}/{resena_id}")
def destacar_resena(hotel_id: str, resena_id: str, datos: dict):
    admin_id = datos.get("admin_id")
    if not admin_id:
        raise HTTPException(status_code=400, detail="Se requiere admin_id")

    resena = resenas.find_one({"_id": ObjectId(resena_id), "id_hotel": int(hotel_id), "estado": "publicada"})
    if not resena:
        raise HTTPException(status_code=404, detail="Reseña no encontrada en este hotel")

    # Quitar destacada anterior del mismo hotel
    resenas.update_many(
        {"id_hotel": int(hotel_id), "destacada": True},
        {"$set": {"destacada": False}}
    )

    # Marcar nueva destacada
    resenas.update_one(
        {"_id": ObjectId(resena_id)},
        {"$set": {"destacada": True, "fecha_actualizacion": datetime.now(timezone.utc).isoformat()}}
    )
    return {"mensaje": "Reseña marcada como destacada"}


# RFC1 – Top 10 hoteles por calificación en un período

@app.get("/analytics/top-hoteles")
def top_hoteles(
    fecha_inicio: str = Query(..., description="Formato: YYYY-MM-DD"),
    fecha_fin: str = Query(..., description="Formato: YYYY-MM-DD")
):
    inicio = datetime.fromisoformat(fecha_inicio)
    fin = datetime.fromisoformat(fecha_fin).replace(hour=23, minute=59, second=59)

    pipeline = [
        {"$match": {
            "estado": "publicada",
            "fecha_creacion": {"$gte": inicio, "$lte": fin}
        }},
        {"$group": {
            "_id": {
                "id_hotel": "$id_hotel",
                "nombre_hotel": "$nombre_hotel",
                "ciudad_hotel": "$ciudad_hotel"
            },
            "calificacion_promedio": {"$avg": "$calificacion"},
            "total_resenas": {"$sum": 1}
        }},
        {"$sort": {"calificacion_promedio": DESCENDING, "total_resenas": DESCENDING}},
        {"$limit": 10},
        {"$project": {
            "_id": 0,
            "id_hotel": "$_id.id_hotel",
            "nombre_hotel": "$_id.nombre_hotel",
            "ciudad_hotel": "$_id.ciudad_hotel",
            "calificacion_promedio": {"$round": ["$calificacion_promedio", 2]},
            "total_resenas": 1
        }}
    ]
    return list(resenas.aggregate(pipeline))


# RFC2 – Evolución de reputación de un hotel mes a mes

@app.get("/analytics/hoteles/{hotel_id}/evolucion")
def evolucion_hotel(hotel_id: int, anio: int = Query(...)):
    inicio = datetime(anio, 1, 1, tzinfo=timezone.utc)
    fin = datetime(anio, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

    pipeline = [
        {"$match": {
            "estado": "publicada",
            "id_hotel": hotel_id,
            "fecha_creacion": {"$gte": inicio, "$lte": fin}
        }},
        {"$group": {
            "_id": {
                "mes": {"$month": "$fecha_creacion"},
                "anio": {"$year": "$fecha_creacion"}
            },
            "calificacion_promedio": {"$avg": "$calificacion"},
            "total_resenas": {"$sum": 1}
        }},
        {"$sort": {"_id.mes": ASCENDING}},
        {"$project": {
            "_id": 0,
            "anio": "$_id.anio",
            "mes": "$_id.mes",
            "calificacion_promedio": {"$round": ["$calificacion_promedio", 2]},
            "total_resenas": 1
        }}
    ]
    return list(resenas.aggregate(pipeline))

# RFC3 – Perfil comparativo de hoteles por ciudad

@app.get("/analytics/ciudades/{ciudad}/comparativo")
def comparativo_ciudad(ciudad: str):
    pipeline = [
        {"$match": {"ciudad_hotel": ciudad}},
        {"$group": {
            "_id": {
                "id_hotel": "$id_hotel",
                "nombre_hotel": "$nombre_hotel",
                "ciudad_hotel": "$ciudad_hotel"
            },
            "calificacion_promedio": {"$avg": "$calificacion"},
            "total_resenas": {"$sum": 1},
            "resenas_con_respuesta": {
                "$sum": {"$cond": [{"$ne": ["$respuesta_admin", None]}, 1, 0]}
            },
            "resenas_destacadas": {
                "$sum": {"$cond": ["$destacada", 1, 0]}
            }
        }},
        {"$group": {
            "_id": "$_id.ciudad_hotel",
            "promedio_ciudad": {"$avg": "$calificacion_promedio"},
            "hoteles": {
                "$push": {
                    "id_hotel": "$_id.id_hotel",
                    "nombre_hotel": "$_id.nombre_hotel",
                    "calificacion_promedio": {"$round": ["$calificacion_promedio", 2]},
                    "total_resenas": "$total_resenas",
                    "pct_con_respuesta": {
                        "$round": [
                            {"$multiply": [{"$divide": ["$resenas_con_respuesta", "$total_resenas"]}, 100]},
                            1
                        ]
                    },
                    "pct_destacadas": {
                        "$round": [
                            {"$multiply": [{"$divide": ["$resenas_destacadas", "$total_resenas"]}, 100]},
                            1
                        ]
                    }
                }
            }
        }},
        {"$project": {
            "_id": 0,
            "ciudad": "$_id",
            "promedio_ciudad": {"$round": ["$promedio_ciudad", 2]},
            "hoteles": 1
        }}
    ]
    resultado = list(resenas.aggregate(pipeline))
    return resultado[0] if resultado else {"ciudad": ciudad, "promedio_ciudad": None, "hoteles": []}

@app.get("/")
def inicio():
    return {"estado": "Dann-Alpes API funcionando correctamente"}
