import re
import os
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from flask_wtf import CSRFProtect
from functools import wraps
from flask import abort


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated

load_dotenv()


def get_youtube_embed_url(url):
    if not url:
        return None

    match = re.search(r'(?:v=|youtu\.be/|embed/)([a-zA-Z0-9_-]{11})', url)
    if not match:
        return None

    return f'https://www.youtube.com/embed/{match.group(1)}'


app = Flask(__name__)

password = os.getenv("MYSQL_PASSWORD")

app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://root:{password}@localhost/miranex'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "fallback-secret-key")
csrf = CSRFProtect(app)

login_manager = LoginManager(app)

@app.context_processor
def inject_user():
    return dict(current_user=current_user)

login_manager.login_view = 'login'

db = SQLAlchemy(app)

class Anime(db.Model):
    __tablename__ = 'anime'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    genres = db.Column(db.Text)
    type = db.Column(db.String(30))
    format = db.Column(db.String(20))
    image = db.Column(db.String(255))
    detail_image = db.Column(db.String(255))
    description = db.Column(db.Text)
    seasons = db.Column(db.Integer)
    episodes = db.Column(db.Integer)
    episode_length = db.Column(db.String(20))
    rating = db.Column(db.Float)
    release_year = db.Column(db.Integer)
    trailer_url = db.Column(db.String(255))

class User(db.Model, UserMixin):
    __tablename__ = 'users'

    user_id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(30), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)

    def set_password(self, raw_password):
        self.password = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password, raw_password)

    def get_id(self):
        return str(self.user_id)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class Watchlist(db.Model):
    __tablename__ = 'watchlist'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'))
    anime_id = db.Column(db.Integer, db.ForeignKey('anime.id'))

    __table_args__ = (db.UniqueConstraint('user_id', 'anime_id'),)


class Watched(db.Model):
    __tablename__ = 'watched'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'))
    anime_id = db.Column(db.Integer, db.ForeignKey('anime.id'))

    __table_args__ = (db.UniqueConstraint('user_id', 'anime_id'),)

class Comment(db.Model):
    __tablename__ = 'comments'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'))
    anime_id = db.Column(db.Integer, db.ForeignKey('anime.id'))
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=None)

    user = db.relationship('User')
    anime = db.relationship('Anime')

class Rating(db.Model):
    __tablename__ = 'ratings'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'))
    anime_id = db.Column(db.Integer, db.ForeignKey('anime.id'))
    score = db.Column(db.Integer, nullable=False)

    __table_args__ = (db.UniqueConstraint('user_id', 'anime_id'),)


TYPE_GENRE_MAP = {
    'Happy' : ['Comedy', 'Slice of Life'],
    'Funny' : ['Comedy'],
    'Emotional' : ['Drama'],
    'Exciting' : ['Action', 'Adventure'],
    'Relaxing' : ['Iyashikei', 'Slice of Life'],
    'Scary' : ['Horror'],
    'Romantic' : ['Romance'],
    'Mysterious' : ['Mystery'],
    'Competitive' : ['Martial Arts', 'Sports']
}

TIME_BUCKETS = {
    'Quick Watch': (1, 4),
    'Weekend': (5, 13),
    'Week': (14, 26),
    'Month': (27, 50),
    'Long Haul': (51, None)
}


BEGINNER_MAX_EPISODES = 24
BEGINNER_GENRES = ['Slice of Life', 'Comedy', 'Romance']
BEGINNER_EXCLUDE_GENRES = ['Drama', 'Psychological', 'Horror']


def apply_filters(query):
    search = request.args.get('search')
    types = request.args.getlist('type')
    formats = request.args.getlist('format')
    genres = request.args.getlist('genre')
    times = request.args.getlist('time')

    if search:
        matching_ids = normalized_match_ids(search)

        if matching_ids:
            query = query.filter(db.or_(Anime.name.contains(search), Anime.id.in_(matching_ids)))
        else:
            query = query.filter(Anime.name.contains(search))

    if types:
        for t in types:
            mapped_genres = TYPE_GENRE_MAP.get(t, [])
            if mapped_genres:
                query = query.filter(db.or_(*[Anime.genres.contains(g) for g in mapped_genres]))

    if formats:
        for f in formats:
            query = query.filter(Anime.format == f)

    if genres:
        for g in genres:
            query = query.filter(Anime.genres.contains(g))

    exclude_genres = request.args.getlist('exclude_genre')
    if exclude_genres:
        for g in exclude_genres:
            query = query.filter(~Anime.genres.contains(g))

    if times:
        time_conditions = []
        for t in times:
            bucket = TIME_BUCKETS.get(t)
            if not bucket:
                continue
            low, high = bucket
            if high is None:
                time_conditions.append(Anime.episodes >= low)
            else:
                time_conditions.append(db.and_(Anime.episodes >= low, Anime.episodes <= high))

        if time_conditions:
            query = query.filter(db.or_(*time_conditions))

    return query


def normalized_match_ids(search):
    search_normalized = re.sub(r'[^a-z0-9]', '', search.lower())
    if not search_normalized:
        return []

    all_anime = Anime.query.with_entities(Anime.id, Anime.name).all()

    matches = []
    for anime_id, name in all_anime:
        name_normalized = re.sub(r'[^a-z0-9]', '', name.lower())
        if search_normalized in name_normalized:
            matches.append(anime_id)

    return matches


@app.route('/')
def home():
    top_rated = Anime.query.order_by(Anime.rating.desc()).limit(5).all()

    beginner_friendly = (
        Anime.query
        .filter(Anime.episodes <= BEGINNER_MAX_EPISODES)
        .filter(db.or_(*[Anime.genres.contains(g) for g in BEGINNER_GENRES]))
        .filter(db.and_(*[~Anime.genres.contains(g) for g in BEGINNER_EXCLUDE_GENRES]))
        .order_by(Anime.rating.desc())
        .limit(5)
        .all()
    )

    quick_low, quick_high = TIME_BUCKETS['Quick Watch']
    quick_watch = (
        Anime.query
        .filter(Anime.episodes >= quick_low, Anime.episodes <= quick_high)
        .order_by(Anime.rating.desc())
        .limit(5)
        .all()
    )

    return render_template(
        'home.html',
        top_rated=top_rated,
        beginner_friendly=beginner_friendly,
        quick_watch=quick_watch
    )


@app.route('/browse')
def browse():
    query = apply_filters(Anime.query)

    if request.args.get('beginner'):
        query = (
            query
            .filter(Anime.episodes <= BEGINNER_MAX_EPISODES)
            .filter(db.or_(*[Anime.genres.contains(g) for g in BEGINNER_GENRES]))
            .filter(db.and_(*[~Anime.genres.contains(g) for g in BEGINNER_EXCLUDE_GENRES]))
        )

    SORT_OPTIONS = {
        'rating_desc': Anime.rating.desc(),
        'rating_asc': Anime.rating.asc(),
        'name_asc': Anime.name.asc(),
        'name_desc': Anime.name.desc(),
        'year_desc': Anime.release_year.desc(),
        'year_asc': Anime.release_year.asc(),
        'episodes_desc': Anime.episodes.desc(),
        'episodes_asc': Anime.episodes.asc()
    }

    sort = request.args.get('sort')
    if sort in SORT_OPTIONS:
        query = query.order_by(SORT_OPTIONS[sort])

    page = request.args.get('page', 1, type=int)
    pagination = query.paginate(page=page, per_page=20, error_out=False)

    filter_args = request.args.to_dict(flat=False)
    filter_args.pop('page', None)

    return render_template(
        'browse.html',
        anime_list=pagination.items,
        pagination=pagination,
        filter_args=filter_args,
        current_sort=sort
    )


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    errors = {}
    username = ''
    email = ''

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not username:
            errors['username'] = 'Username is required.'
        elif len(username) > 20:
            errors['username'] = 'Username must be 20 characters or fewer.'

        if not email:
            errors['email'] = 'Email is required.'
        elif len(email) > 30:
            errors['email'] = 'Email must be 30 characters or fewer.'
        elif not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
            errors['email'] = 'Please enter a valid email address.'
        elif User.query.filter_by(email=email).first():
            errors['email'] = 'An account with that email already exists.'

        if not password:
            errors['password'] = 'Password is required.'
        elif len(password) < 8:
            errors['password'] = 'Password must be at least 8 characters.'
        elif not re.search(r'[A-Za-z]', password) or not re.search(r'[0-9]', password):
            errors['password'] = 'Password must contain both letters and numbers.'

        if not confirm_password:
            errors['confirm_password'] = 'Please confirm your password.'
        elif password != confirm_password:
            errors['confirm_password'] = 'Passwords do not match.'

        if not errors:
            new_user = User(username=username, email=email)
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()

            login_user(new_user)
            return redirect(url_for('home'))

    return render_template('signup.html', errors=errors, username=username, email=email)

@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    total_users = User.query.count()
    total_anime = Anime.query.count()
    total_comments = Comment.query.count()
    total_watchlist = Watchlist.query.count()
    total_watched = Watched.query.count()

    anime_search = request.args.get('anime_search', '').strip()

    if anime_search or request.args.get('tab') == 'anime':
        active_tab = 'anime'
    else:
        active_tab = 'moderation'

    comment_search = request.args.get('comment_search', '').strip()

    comment_query = Comment.query
    if comment_search:
        comment_query = comment_query.join(User).filter(
        db.or_(
            Comment.content.contains(comment_search),
            User.username.contains(comment_search)
        )
    )

    comment_query = comment_query.order_by(Comment.created_at.desc())
    comment_page = request.args.get('comment_page', 1, type=int)
    comment_pagination = comment_query.paginate(page=comment_page, per_page=10, error_out=False)

    comment_filter_args = request.args.to_dict(flat=False)
    comment_filter_args.pop('anime_page', None)
    comment_filter_args.pop('comment_page', None)
    comment_filter_args['tab'] = ['moderation']

    anime_query = Anime.query
    if anime_search:
        matching_ids = normalized_match_ids(anime_search)
        if matching_ids:
            anime_query = anime_query.filter(
                db.or_(Anime.name.contains(anime_search), Anime.id.in_(matching_ids))
            )
        else:
            anime_query = anime_query.filter(Anime.name.contains(anime_search))

    anime_query = anime_query.order_by(Anime.id.desc())
    anime_page = request.args.get('anime_page', 1, type=int)
    anime_pagination = anime_query.paginate(page=anime_page, per_page=10, error_out=False)

    anime_filter_args = request.args.to_dict(flat=False)
    anime_filter_args.pop('anime_page', None)
    anime_filter_args.pop('comment_page', None)
    anime_filter_args['tab'] = ['anime']


    return render_template(
        'admin.html',
        total_users=total_users,
        total_anime=total_anime,
        total_comments=total_comments,
        total_watchlist=total_watchlist,
        total_watched=total_watched,
        comments=comment_pagination.items,
        comment_pagination=comment_pagination,
        comment_filter_args=comment_filter_args,
        anime_list=anime_pagination.items,
        anime_pagination=anime_pagination,
        anime_filter_args=anime_filter_args,
        anime_search=anime_search,
        comment_search=comment_search,
        active_tab=active_tab
    )


@app.route('/admin/comment/<int:comment_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    db.session.delete(comment)
    db.session.commit()
    return redirect(url_for('admin_dashboard', tab='moderation'))


@app.route('/admin/anime/add', methods=['POST'])
@login_required
@admin_required
def admin_add_anime():
    new_anime = Anime(
        name=request.form.get('name', '').strip(),
        genres=request.form.get('genres', '').strip(),
        type=request.form.get('type', '').strip(),
        format=request.form.get('format', '').strip(),
        image=request.form.get('image', '').strip(),
        detail_image=request.form.get('detail_image', '').strip(),
        description=request.form.get('description', '').strip(),
        seasons=request.form.get('seasons', type=int),
        episodes=request.form.get('episodes', type=int),
        episode_length=request.form.get('episode_length', '').strip(),
        rating=request.form.get('rating', type=float),
        release_year=request.form.get('release_year', type=int),
        trailer_url=request.form.get('trailer_url', '').strip()
    )
    db.session.add(new_anime)
    db.session.commit()
    return redirect(url_for('admin_dashboard', tab='anime'))


@app.route('/admin/anime/<int:anime_id>/edit', methods=['POST'])
@login_required
@admin_required
def admin_edit_anime(anime_id):
    anime = Anime.query.get_or_404(anime_id)
    anime.name = request.form.get('name', '').strip()
    anime.genres = request.form.get('genres', '').strip()
    anime.type = request.form.get('type', '').strip()
    anime.format = request.form.get('format', '').strip()
    anime.image = request.form.get('image', '').strip()
    anime.detail_image = request.form.get('detail_image', '').strip()
    anime.description = request.form.get('description', '').strip()
    anime.seasons = request.form.get('seasons', type=int)
    anime.episodes = request.form.get('episodes', type=int)
    anime.episode_length = request.form.get('episode_length', '').strip()
    anime.rating = request.form.get('rating', type=float)
    anime.release_year = request.form.get('release_year', type=int)
    anime.trailer_url = request.form.get('trailer_url', '').strip()
    
    db.session.commit()
    return redirect(url_for('admin_dashboard', tab='anime'))



@app.route('/admin/anime/<int:anime_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_anime(anime_id):
    Watchlist.query.filter_by(anime_id=anime_id).delete()
    Watched.query.filter_by(anime_id=anime_id).delete()
    Comment.query.filter_by(anime_id=anime_id).delete()
    Anime.query.filter_by(id=anime_id).delete()
    db.session.commit()
    return redirect(url_for('admin_dashboard', tab='anime'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()

        if user is None or not user.check_password(password):
            flash('Incorrect email or password.')
            return redirect(url_for('login'))

        login_user(user)
        return redirect(url_for('home'))

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route('/delete-account', methods=['POST'])
@login_required
def delete_account():
    user_id = current_user.user_id

    Watchlist.query.filter_by(user_id=user_id).delete()
    Watched.query.filter_by(user_id=user_id).delete()
    Comment.query.filter_by(user_id=user_id).delete()

    user = User.query.get(user_id)
    logout_user()
    db.session.delete(user)
    db.session.commit()

    return redirect(url_for('home'))


@app.errorhandler(403)
def access_forbidden(e):
    return render_template('403.html'), 403


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html')


@app.route('/anime/<int:id>', methods=['GET', 'POST'])
def anime_detail(id):
    anime = Anime.query.get_or_404(id)
    trailer_embed_url = get_youtube_embed_url(anime.trailer_url)

    if request.method == 'POST':
        if not current_user.is_authenticated:
            return redirect(url_for('login'))

        content = request.form.get('content', '').strip()
        if content:
            new_comment = Comment(user_id=current_user.user_id, anime_id=id, content=content)
            db.session.add(new_comment)
            db.session.commit()

        return redirect(url_for('anime_detail', id=id))

    in_watchlist = False
    in_watched = False
    user_rating = None
    if current_user.is_authenticated:
        in_watchlist = Watchlist.query.filter_by(user_id=current_user.user_id, anime_id=id).first() is not None
        in_watched = Watched.query.filter_by(user_id=current_user.user_id, anime_id=id).first() is not None

        existing_rating = Rating.query.filter_by(user_id=current_user.user_id, anime_id=id).first()
        user_rating = existing_rating.score if existing_rating else None

    rating_avg = db.session.query(db.func.avg(Rating.score)).filter_by(anime_id=id).scalar()
    rating_count = Rating.query.filter_by(anime_id=id).count()

    anime_genres = set(g.strip() for g in anime.genres.split(',') if g.strip())

    MIN_SHARED_GENRES = 2

    candidates_query = Anime.query.filter(Anime.id != anime.id)

    if anime.format == 'Movie':
        candidates_query = candidates_query.filter(Anime.format == 'Movie')
    else:
        candidates_query = candidates_query.filter(Anime.format != 'Movie')

    candidates = (
        candidates_query
        .filter(db.or_(*[Anime.genres.contains(g) for g in anime_genres]))
        .all()
    )

    def shared_genre_count(other):
        other_genres = set(g.strip() for g in other.genres.split(',') if g.strip())
        return len(anime_genres & other_genres)

    scored = [(c, shared_genre_count(c)) for c in candidates]
    scored = [pair for pair in scored if pair[1] >= MIN_SHARED_GENRES]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    recommended = [c for c, _ in scored[:6]]

    comment_limit = request.args.get('comments', 5, type=int)

    pinned_comments = []
    if current_user.is_authenticated:
        pinned_comments = (
        Comment.query
        .filter_by(anime_id=id, user_id=current_user.user_id)
        .order_by(Comment.created_at.desc())
        .all()
    )

    pinned_ids = [c.id for c in pinned_comments]

    other_comments = (
        Comment.query
        .filter_by(anime_id=id)
        .filter(~Comment.id.in_(pinned_ids))
        .order_by(Comment.created_at.desc())
        .limit(comment_limit)
        .all()
    )

    comments = pinned_comments + other_comments
    shown_count = len(comments)
    total_comments = Comment.query.filter_by(anime_id=id).count()

    return render_template(
        'anime_detail.html',
        anime=anime,
        in_watchlist=in_watchlist,
        in_watched=in_watched,
        recommended=recommended,
        comments=comments,
        total_comments=total_comments,
        shown_count=shown_count,
        comment_limit=comment_limit,
        user_rating=user_rating,
        community_rating_avg=round(rating_avg, 1) if rating_avg else None,
        rating_count=rating_count,
        trailer_embed_url=trailer_embed_url
    )


@app.route('/anime/<int:id>/toggle-list', methods=['POST'])
@login_required
def toggle_list(id):
    list_type = request.form.get('list_type')
    Anime.query.get_or_404(id)

    model = Watchlist if list_type == 'watchlist' else Watched if list_type == 'watched' else None
    other_model = Watched if list_type == 'watchlist' else Watchlist if list_type == 'watched' else None

    if model is None:
        return redirect(url_for('anime_detail', id=id))

    existing = model.query.filter_by(user_id=current_user.user_id, anime_id=id).first()
    newly_watched = False

    if existing:
        db.session.delete(existing)
    else:
        other_existing = other_model.query.filter_by(user_id=current_user.user_id, anime_id=id).first()
        if other_existing:
            db.session.delete(other_existing)

        db.session.add(model(user_id=current_user.user_id, anime_id=id))
        if list_type == 'watched':
            newly_watched = True

    db.session.commit()

    if newly_watched:
        return redirect(url_for('anime_detail', id=id, just_watched=1))

    return redirect(url_for('anime_detail', id=id))


@app.route('/comment/<int:comment_id>/edit', methods=['POST'])
@login_required
def edit_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)

    if comment.user_id != current_user.user_id:
        return redirect(url_for('anime_detail', id=comment.anime_id))

    new_content = request.form.get('content', '').strip()
    if new_content and new_content != comment.content:
        comment.content = new_content
        comment.updated_at = datetime.utcnow()
        db.session.commit()

    return redirect(url_for('anime_detail', id=comment.anime_id))


@app.route('/comment/<int:comment_id>/delete', methods=['POST'])
@login_required
def delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)

    if comment.user_id != current_user.user_id:
        return redirect(url_for('anime_detail', id=comment.anime_id))

    anime_id = comment.anime_id
    db.session.delete(comment)
    db.session.commit()

    return redirect(url_for('anime_detail', id=anime_id))

@app.route('/random')
def random_anime():
    query = apply_filters(Anime.query)

    if request.args.get('beginner'):
        query = (
            query
            .filter(Anime.episodes <= BEGINNER_MAX_EPISODES)
            .filter(db.or_(*[Anime.genres.contains(g) for g in BEGINNER_GENRES]))
            .filter(db.and_(*[~Anime.genres.contains(g) for g in BEGINNER_EXCLUDE_GENRES]))
        )

    anime = query.order_by(db.func.rand()).first()

    if anime is None:
        return redirect(url_for('browse', **request.args))

    return redirect(url_for('anime_detail', id=anime.id))


@app.route('/watchlist')
@login_required
def watchlist():
    entries = Watchlist.query.filter_by(user_id=current_user.user_id).all()
    anime_ids = [e.anime_id for e in entries]
    anime_list = Anime.query.filter(Anime.id.in_(anime_ids)).all() if anime_ids else []
    watch_count = len(anime_list)
    return render_template('watchlist.html',
                            anime_list=anime_list,
                            watch_count=watch_count
                            )


@app.route('/watched')
@login_required
def watched():
    entries = Watched.query.filter_by(user_id=current_user.user_id).all()
    anime_ids = [e.anime_id for e in entries]
    anime_list = Anime.query.filter(Anime.id.in_(anime_ids)).all() if anime_ids else []
    watched_count = len(anime_list)
    return render_template('watched.html',
                           anime_list=anime_list,
                           watched_count=watched_count
                           )


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/privacy')
def privacy():
    return render_template('privacy.html')


@app.route('/anime/<int:id>/rate', methods=['POST'])
@login_required
def rate_anime(id):
    Anime.query.get_or_404(id)

    in_watched = Watched.query.filter_by(user_id=current_user.user_id, anime_id=id).first() is not None
    if not in_watched:
        return redirect(url_for('anime_detail', id=id))

    score = request.form.get('score', type=int)
    if score is None or score < 1 or score > 5:
        return redirect(url_for('anime_detail', id=id))

    existing = Rating.query.filter_by(user_id=current_user.user_id, anime_id=id).first()
    if existing:
        existing.score = score
    else:
        db.session.add(Rating(user_id=current_user.user_id, anime_id=id, score=score))

    db.session.commit()
    return redirect(url_for('anime_detail', id=id) + '#community-rating')


if __name__ == "__main__":
    app.run(debug=True, threaded=True)