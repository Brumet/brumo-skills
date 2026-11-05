"""Una hoja de cálculo en memoria con la misma forma que el Google Sheets real."""
import json

from brumo.negocio import pedidos as P


class HojaFalsa:
    def __init__(self):
        self.hojas = {
            P.HOJA_VENTAS: (list(P.HEADERS_VENTAS), []),
            P.HOJA_GASTOS: (list(P.HEADERS_GASTOS), []),
            P.HOJA_PRODUCTOS: (["nombre", "precio_base", "costo_estimado", "horas", "gramos", "material", "mililitros", "fecha_creacion"], []),
        }

    def leer(self, hoja):
        headers, filas = self.hojas[hoja]
        return list(headers), [{**f, "_fila": i + 2} for i, f in enumerate(filas)]

    def actualizar(self, hoja, fila, headers, cambios):
        self.hojas[hoja][1][fila - 2].update(cambios)

    def agregar(self, hoja, headers, objeto):
        self.hojas[hoja][1].append({h: objeto.get(h, "") for h in headers})
        return len(self.hojas[hoja][1]) + 1  # número de fila, como Google (la 1 son los encabezados)


def con_pedidos():
    h = HojaFalsa()
    ventas = h.hojas[P.HOJA_VENTAS][1]
    ventas.append({"numero_pedido": "PED-2026-0026", "cliente_nombre": "Ana Pérez", "estado": "Entregado",
                   "valor": 50000, "abono": 50000, "forma_pago": "Pagado", "fecha_pedido": "2026-10-01",
                   "tiempo_entrega": "5-7 días", "items_json": json.dumps([{"nombre": "Llavero", "cantidad": 2}])})
    ventas.append({"numero_pedido": "PED-2026-0027", "cliente_nombre": "Juan Gómez", "cliente_telefono": "3001234567",
                   "estado": "En fabricacion", "valor": 180000, "abono": 90000, "forma_pago": "Abono",
                   "saldo_confirmado": "FALSE", "fecha_pedido": "2026-10-20", "tiempo_entrega": "5-7 días",
                   "ciudad": "Medellín", "direccion": "Cra 1 # 2-3",
                   "items_json": json.dumps([{"nombre": "Máscara Oni", "cantidad": 1, "nota": "rojo mate", "pp": "pintado a mano"}])})
    ventas.append({"numero_pedido": "PED-2026-0028", "cliente_nombre": "Luisa", "estado": "Pendiente",
                   "valor": 60000, "abono": 0, "forma_pago": "Contra entrega", "fecha_pedido": "2026-10-24",
                   "tiempo_entrega": "15-20 días hábiles", "producto_nombre": "Lámpara luna"})
    ventas.append({"numero_pedido": "PED-2025-0027", "cliente_nombre": "Viejo", "estado": "Entregado"})
    h.hojas[P.HOJA_PRODUCTOS][1].append({"nombre": "Máscara Oni", "horas": 14, "gramos": 320, "material": "Filamento"})
    return h
