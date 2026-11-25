# ADR-010 — Directorio de usuarios tipo Active Directory (LDAP)

## Contexto

El PDF del equipo pide autenticar a los empleados contra Active Directory. AWS Managed Microsoft AD
tiene un costo fijo por hora alto para el crédito del Learner Lab y un controlador de dominio Windows no
cabe en una t3.small.

## Alternativas

- **Directorio LDAP en contenedor (OpenLDAP)** con la misma forma que un AD: usuarios en una unidad
  organizativa y un grupo por rol. Liviano y con el mismo protocolo que AD.
- Samba AD DC: un AD real en contenedor. Más fiel, pero más pesado y frágil de configurar en una t3.small.
- Amazon Cognito: administrado y disponible en el Learner Lab, pero no es AD ni habla LDAP.

## Decisión

OpenLDAP en el compose de la central (`central_ldap`, `dc=bodegas,dc=local`) y el proveedor
`LdapDirectory` (`central/ldap_directory.py`) detrás de la interfaz `IdentityProvider`:

| Paso | Cómo |
|---|---|
| Inicio de sesión | La cuenta de servicio busca al usuario (`uid`), su contraseña se valida con un bind propio y la central firma el token |
| Rol | Sale del grupo: `Administradores-Globales`, `Lideres`, `Administradores-WH-00X`, `Registradores-WH-00X`; si hay varios, gana el de más alcance |
| Alta de credenciales | El Admin global crea el usuario en el directorio con contraseña temporal (`{SSHA}`) y lo agrega al grupo; una bodega nueva crea sus dos grupos |
| Perfil | La tabla `users` de la central guarda rol, bodega, activo y último acceso; nunca la contraseña |
| Directorio caído | 503 ("el directorio no responde"); los tokens ya emitidos siguen sirviendo en las bodegas |

Con un AD corporativo basta cambiar la configuración: `LDAP_URL`, `LDAP_USER_ATTRIBUTE=sAMAccountName`,
`LDAP_USER_OBJECT_CLASS=user`, `LDAP_GROUP_OBJECT_CLASS=group` y `LDAP_MANAGE_USERS=false` (las altas y
contraseñas las hace TI en el AD). `IDENTITY_PROVIDER=local` vuelve al directorio de la tabla `users`.

## Consecuencias

- Las contraseñas viven en el directorio, no en la base de la central.
- La búsqueda escapa el nombre de usuario (sin inyección LDAP) y una contraseña vacía nunca se acepta
  (en LDAP sería un bind anónimo válido).
- Un contenedor liviano más en la central. En AWS la clave del administrador del directorio y
  la de los usuarios iniciales se derivan del secreto maestro de Terraform.
- Sin réplica: si el contenedor del directorio cae, nadie nuevo inicia sesión hasta que vuelva.
