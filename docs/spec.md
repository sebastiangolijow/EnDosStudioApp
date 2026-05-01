# Proyecto StickerApp: Guía de Arquitectura y Tecnologías

## 1. Resumen del Proyecto

Creamos una aplicación web para una gráfica, donde los clientes pueden subir archivos (imágenes: PNG/JPG) para mandar a imprimir stickers personalizados. El sistema debe permitir:

- Registro e inicio de sesión de usuarios.
- Subida de imágenes.
- Recorte automático del sticker detectando los bordes del diseño (debe sugerir el perfil del sticker).
- Herramienta para marcar zonas de “relieve” sobre la imagen (opcional por el usuario).
- Flujo de pedido con pasarela de pago.
- Panel para gestionar pedidos realizados.

## 2. Stack Tecnológico

### Backend (a reutilizar)
- **Django**:  
  - Usuarios/autenticación/clientes.
  - Modelos de pedidos.
  - Gestión y almacenamiento de archivos subidos.
  - API REST (preferentemente con Django Rest Framework).
  - Integración con la pasarela de pago.
  - (Opcional) Si la imagen necesita procesamiento pesado: recorte con OpenCV en Python (FUTURE).

### Frontend (énfasis especial)
- **Vue.js (SPA)**:  
  - Login, registro, dashboard, integración completa con el backend Django.
  - Módulo especial para edición de stickers:

    #### Editor de Stickers:
    - Subida y previsualización de la imagen que sube el usuario.
    - Recorte automático de bordes usando **OpenCV.js** (procesamiento de bordes y creación de máscara, del lado del navegador).
    - Herramienta de dibujo sobre el canvas para que el usuario marque las zonas de “relieve” (zonas especiales del sticker que serán indicadas en el pedido).

### Otras Librerías Frontend
- **OpenCV.js**: para análisis y procesamiento de imágenes en el navegador.
- **Canvas API** (HTML5): para desplegar la imagen original y superponer las selecciones/máscaras del usuario.
- (Opcional): Fabric.js u otra lib para hacer más sencilla la edición/dibujo sobre el canvas.

## 3. Qué se reutiliza del Backend Django existente

- Sistemas de usuarios/autenticación.
- Infraestructura básica de modelos, endpoints de API REST, pedidos, subida de archivos.
- Integración de la pasarela de pago y flujos de pedidos.
- Infraestructura de almacenamiento de archivos/media.

Solo se requiere implementar endpoints que permitan:
- Subir la imagen del sticker y las zonas de relieve marcadas (máscaras en formato PNG/JSON).
- Guardar el resultado final del recorte o procesarlo en backend si el cliente lo solicita.

## 4. Enfoque específico en el Frontend y OpenCV.js

### 4.1 Flujo Frontend del Editor de Stickers

1. **Usuario sube una imagen** (PNG/JPG).
2. Imagen se muestra en un `<canvas>`.
3. Al hacer clic en “Recortar automáticamente”:
    - Con **OpenCV.js** en el frontend:
      - Convertir imagen a escala de grises.
      - Detección de bordes con Canny.
      - Encontrar contornos.
      - Extraer el contorno más grande.
      - Dibujar el perfil/máscara del recorte sobre el canvas (previsualizar y, si el usuario confirma, guardar esa máscara).
4. **Herramienta de selección de “zonas de relieve”**:
    - El usuario puede seleccionar, pintar o dibujar zonas extras sobre el canvas (por ejemplo, usando el mouse para marcar áreas específicas).
    - Guardar esta información en un formato sencillo (puede ser otra imagen PNG semitransparente o datos vectoriales si es simple).
    - Enviar ambos archivos (recorte y máscara de relieve) al backend cuando se confirma el pedido.

### 4.2 Integración OpenCV.js en Vue

- OpenCV.js se carga vía CDN en el `index.html` de la app Vue.
- El editor de stickers es un componente Vue independiente.
- El componente verifica que OpenCV.js esté cargado antes de permitir recortar.
- El Canvas/Canvas+Fabric.js dibuja la imagen, el perfil de recorte y la/s zonas/polígonos de relieve seleccionadas.
- Cuando el usuario confirma:
    - Se genera la máscara de recorte automáticamente.
    - Se genera la máscara o los datos de las zonas de relieve.
    - Se suben ambos (junto con el pedido) al backend Django vía API.

#### Ejemplo de estructura para el editor:

```plaintext
<StickerEditor>
  - <input type="file"> para subir imagen
  - <canvas> para mostrar imagen y perfiles
  - [Botón] Recortar automáticamente (OpenCV.js)
  - [Herramienta dibujo] Marcar zonas de relieve (canvas interactivo)
  - [Botón] Confirmar pedido (sube imagen, máscara de recorte y máscara de relieve)
</StickerEditor>
```

#### Diagrama de flujo simplificado
```
[Usuario] --> [Sube imagen] --(OpenCV.js)--> [Recorte auto + Selección zonas relieve] --> [Confirma] --> [API Django]
```

### 5. Ventajas de este enfoque

- El recorte y selección base se hacen en el frontend (rápido, no carga backend).
- Todo el flujo de pago, usuarios y administración sigue igual.
- Solo se requiere agregar nuevos modelos y endpoints para guardar las máscaras de relieve/recorte y asociarlas al pedido de stickers.

---

## RESUMEN PARA CLAUDE/Code

- Reutilizamos todo el backend de Django y la gestión de usuarios, almacenamiento y pedidos.
- El **frontend** se potencia agregando un editor visual con recorte automático y selector de relieve usando **Vue, Canvas y OpenCV.js**.
- El procesamiento simple o visual (preparación de imágenes, recorte y marcas de relieve) se hace en **frontend** para mejor UX y menos carga del backend.
- Si la imagen es demasiado compleja, podemos extender el backend con OpenCV-Python usando los mismos algoritmos.

---

**Fin.**