# Daisy — Sujet A : Synchronisation sans surréservation

Ce projet gère la synchronisation des réservations entre Daisy et la plateforme partenaire Artisia, en protégeant les stocks de l'artisan et en évitant que son écran ne rame quand l'API externe plante ou traîne.

---

## PARTIE 1 — Les questions du Sujet A

### 1. Deux clients réservent la dernière place au même instant (Daisy vs Artisia). Que se passe-t-il ? Le système peut-il surréserver ?

**Dans mon code :**

Quand une réservation arrive (via Daisy ou via le webhook Artisia), le code met un verrou strict sur la ligne du cours dans la base de données (`SELECT ... FOR UPDATE`).

PostgreSQL traite les requêtes l'une après l'autre :

1. La première requête prend le verrou, réserve la place restante (stock → 0) et valide.
2. La deuxième attend son tour. Quand le verrou se libère, elle entre, constate qu'il reste 0 place et refuse la vente.
   - **Si la vente venait de Daisy :** le client reçoit une erreur directe « Plus de place » avant d'avoir pu payer.
   - **Si la vente venait d'Artisia :** comme le client a déjà payé chez eux, on refuse de passer le stock à -1. On note la réservation à part avec le statut `conflict_overbooked` pour prévenir l'artisan.

**Est-ce que le système peut surréserver dans la vraie vie ?**

Oui, physiquement. Artisia valide la vente sur son propre site avant d'envoyer son webhook à Daisy. Si un client réserve sur Artisia et un autre sur Daisy à la même seconde, deux personnes ont acheté la même place avant même qu'Artisia ne nous prévienne. Aucun code au monde ne peut empêcher ce décalage temporel entre deux serveurs distants.

**Ce que mon code fait face à ça :**

Il refuse de masquer le problème. Il ne corrompt pas les comptes (pas de stock négatif). Il isole la réservation en trop pour que l'artisan sache exactement qui contacter.

---

### 2. Le partenaire est en panne pendant 20 minutes. Que voit l'artisan ? Au retour du service, comment rattrapes-tu le retard ?

**Pendant la panne :**

L'artisan utilise Daisy normalement. Quand il prend une réservation, l'écran répond instantanément (en moins de 50 ms).

On applique le principe de l'Outbox : on enregistre la réservation dans Daisy et on dépose un ordre de mission dans la table `outbox_tasks` au même moment. Daisy n'attend pas la réponse d'Artisia pour dire « c'est bon » à l'artisan.

**Au retour du service :**

- **De Daisy vers Artisia :** un script dépile les tâches en attente dans `outbox_tasks`. Comme l'API d'Artisia ne permet pas de rejouer des réservations sans risquer de créer des doublons, on fait un tour de vérification (un `GET` sur leurs sessions) pour comparer les états réels avant d'envoyer les mises à jour.
- **D'Artisia vers Daisy :** Artisia rejoue automatiquement ses webhooks en attente. Dès qu'ils arrivent, notre filtre anti-doublon fait le tri et met à jour le planning.

---

### 3. Le même webhook arrive trois fois. Comment tu t'en protèges, et à quel coût ?

**La protection (l'idempotence) :**

Chaque message d'Artisia a un identifiant unique (`event_id`). Quand un message arrive, on essaie d'insérer cet identifiant dans la table `webhook_events`.

- **Si l'ID n'existe pas :** la base l'enregistre, et on traite la réservation.
- **Si l'ID existe déjà :** la base refuse l'insertion (erreur de clé unique). On annule immédiatement (rollback) et on renvoie un code `200 OK` à Artisia pour lui dire « message reçu, arrête d'insister », sans toucher aux places.

**Le coût :**

- **Temps :** une simple écriture en base, invisible pour les performances (moins d'une milliseconde).
- **Espace disque :** une ligne par événement dans la base (quelques octets). Même avec des milliers d'événements par jour, cela pèse quelques dizaines de mégaoctets par an.

**Pourquoi en base et pas dans un cache ?**

Parce qu'enregistrer l'ID du message et modifier les places dans la même base garantit que si le serveur plante au milieu, tout s'annule en même temps. Avec un cache à côté, les deux systèmes risquent de ne plus être d'accord.

---

### 4. « Ne jamais surréserver » vs « Ne jamais bloquer une vente » : que choisis-tu, et que dis-tu à l'artisan ?

**Mon choix : Ne jamais surréserver.**

**Ce que je dis à l'artisan :**

> « Bloquer une vente par prudence vous fait rater un gain potentiel. Mais une surréservation vous coûte de l'argent réel : Artisia garde sa commission même si on annule, vous devez gérer un client mécontent qui se déplace pour rien le samedi, et vous n'avez pas de tour de potier en rab dans l'atelier. Daisy est là pour protéger votre planning et votre réputation, pas pour vous créer des urgences ingérables le week-end. »

---

## PARTIE 2 — Socle commun

### 1. Journal de bord

**Temps passé :** environ 4 heures.

**Ordre de travail :**

1. Lecture des contraintes de l'API Artisia (lenteurs de 3 à 6 secondes, erreurs 500, webhooks envoyés en double).
2. Modélisation de la base (`sessions`, `bookings`, `webhook_events`, `outbox_tasks`) sur Supabase (PostgreSQL).
3. Route du webhook : sécurisation par signature HMAC, filtre anti-doublon (idempotence) et verrouillage des places (`SELECT ... FOR UPDATE`).
4. Réservations locales : utilisation des tâches de fond pour ne jamais bloquer l'artisan quand l'API partenaire rame.
5. Rédaction du retour d'expérience et des choix d'architecture.

**Ce qui m'a bloqué :** un souci de résolution DNS IPv6 avec Supabase sous macOS, corrigé en basculant sur leur pooler de session en IPv4.

**Ce que j'ai laissé de côté :** un gestionnaire de tâches lourd comme Celery ou RabbitMQ. Pour le volume d'un atelier et le cadre du test, les tâches de fond de FastAPI associées à la table `outbox_tasks` suffisent largement sans ajouter d'usines à gaz inutiles.

---

### 2. Tri des tickets

Mon classement (du ticket que je prendrais avec le plus d'envie au moins motivant) :

1. Un partenaire a changé le format de ses dates sans prévenir, les réservations n'entrent plus depuis ce matin.
2. Le calendrier du back-office rame dès qu'un atelier a plus de 200 cours affichés.
3. Le tunnel du widget a un taux d'abandon de 60 % à l'étape des coordonnées, personne ne sait pourquoi.
4. Il faut exposer une API publique propre pour que de futurs partenaires s'intègrent seuls.
5. Trois artisans signalent que l'export comptable ne correspond pas à leurs relevés bancaires.
6. Il faut ajouter les paiements sur place par TPE dans le parcours de réservation.
7. Les artisans réclament un filtre par type de cours dans la liste des réservations.
8. Le design system n'existe pas, chaque écran a ses propres boutons.

**Ce que ce classement dit de moi :**

J'aime résoudre les pannes concrètes, optimiser ce qui rame et concevoir des systèmes fiables sous le capot. Réaligner des boutons ou modifier des formulaires m'intéresse beaucoup moins que de m'assurer que les données et les flux d'argent sont justes.

---

### 3. Mes deux premières semaines dans l'équipe

**Semaine 1 — Observer et comprendre le terrain :**

- Monter l'environnement local de zéro et documenter les blocages pour le prochain arrivant.
- Passer du temps sur le support client pour voir les vrais problèmes des artisans au quotidien.
- Pousser une modification mineure en production pour valider le circuit de déploiement.

**Semaine 2 — Identifier les fragilités :**

- Regarder les logs d'erreurs récurrentes en production.
- Vérifier les requêtes qui ralentissent la base sur les réservations.
- Échanger avec l'équipe sur les deux ou trois points techniques les plus urgents à stabiliser sans vouloir tout refaire.

---

### 4. Ce que j'aurais demandé avant de commencer

**Ma question :**

> Quand une surréservation inévitable a lieu en même temps sur la dernière place entre Daisy et Artisia, quelle est la règle métier : est-ce qu'on annule automatiquement le partenaire pour privilégier le client direct de l'artisan, ou est-ce qu'on prend le premier arrivé à la seconde près ?

**Mon choix par défaut :**

J'ai choisi de ne rien casser automatiquement. La réservation partenaire en trop est marquée en conflit (`conflict_overbooked`), et c'est l'artisan qui tranche en sachant exactement ce qui s'est passé.

---

## Stack technique

| Domaine | Choix |
|---|---|
| **Langage & Framework** | Python 3.13, FastAPI |
| **Base de données** | PostgreSQL (Supabase) via SQLAlchemy |
| **Gestion des conflits** | Verrouillage à la ligne (`SELECT ... FOR UPDATE`) |
| **Résilience API** | Table d'événements pour l'idempotence, table Outbox pour les tâches en arrière-plan |