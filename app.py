import re
import os
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy

load_dotenv()

app = Flask(__name__)

password = os.getenv("MYSQL_PASSWORD")

app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://root:{password}@localhost/miranex'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "fallback-secret-key")

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

class User(db.Model, UserMixin):
    __tablename__ = 'users'

    user_id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(30), nullable=False)
    password = db.Column(db.String(255), nullable=False)

    def set_password(self, raw_password):
        self.password = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password, raw_password)

    def get_id(self):
        return str(self.user_id)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class Wishlist(db.Model):
    __tablename__ = 'wishlist'

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

# TYPE_GENRE_MAP = {
#     'Funny': ['Comedy'],
#     'Soothing': ['Iyashikei', 'Slice of Life'],
#     'Historical': ['Historical'],
#     'Emotional': ['Drama'],
#     'Scary': ['Horror'],
#     'Mysterious': ['Mystery'],
#     'Wholesome': ['Slice of Life', 'Comedy'],
#     'Intense': ['Action', 'Thriller'],
#     'Sweet': ['Romance', 'Iyashikei'],
#     'Dark': ['Horror', 'Psychological'],
#     'Superpowers': ['Supernatural'],
#     'Competitive': ['Sports'],
#     'Another World' : ['Isekai'],
#     'Science Fiction' : ['Sci-Fi'],
#     'School' : ['School'],
#     'Robot' : ['Mecha']
# }

TYPE_GENRE_MAP = {
    'Happy' : ['Comedy', 'Slice of Life'],
    'Funny' : ['Comedy'],
    'Emotional' : ['Drama'],
    'Exciting' : ['Action', 'Adventure'],
    'Relaxing' : ['Iyashikei'],
    'Scary' : ['Horror', 'Psychological'],
    'Romantic' : ['Romance'],
    'Mysterious' : ['Mystery'],
    'Competitive' : ['Martial Arts', 'Sports']
}

TIME_BUCKETS = {
    'Quick Watch': (1, 3),
    'Weekend': (4, 13),
    'Week': (14, 26),
    'Month': (27, 50),
    'Long Haul': (51, None)
}


BEGINNER_MAX_EPISODES = 24
BEGINNER_GENRES = ['Slice of Life', 'Comedy', 'Romance']


def apply_filters(query):
    search = request.args.get('search')
    types = request.args.getlist('type')
    formats = request.args.getlist('format')
    genres = request.args.getlist('genre')
    times = request.args.getlist('time')

    if search:
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


@app.route('/')
def home():
    top_rated = Anime.query.order_by(Anime.rating.desc()).limit(5).all()

    beginner_friendly = (
        Anime.query
        .filter(Anime.episodes <= BEGINNER_MAX_EPISODES)
        .filter(db.or_(*[Anime.genres.contains(g) for g in BEGINNER_GENRES]))
        .filter(Anime.format == 'TV')
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
        )

    if request.args.get('sort') == 'rating':
        query = query.order_by(Anime.rating.desc())

    page = request.args.get('page', 1, type=int)
    pagination = query.paginate(page=page, per_page=20, error_out=False)

    filter_args = request.args.to_dict(flat=False)
    filter_args.pop('page', None)

    return render_template(
        'browse.html',
        anime_list=pagination.items,
        pagination=pagination,
        filter_args=filter_args
    )

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    errors = {}
    username = ''
    email = ''

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
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
        elif len(password) < 6:
            errors['password'] = 'Password must be at least 6 characters.'
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

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
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

    Wishlist.query.filter_by(user_id=user_id).delete()
    Watched.query.filter_by(user_id=user_id).delete()

    user = User.query.get(user_id)
    logout_user()
    db.session.delete(user)
    db.session.commit()

    return redirect(url_for('home'))

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html')

@app.route('/anime/<int:id>')
def anime_detail(id):
    anime = Anime.query.get_or_404(id)

    in_watchlist = False
    in_watched = False
    if current_user.is_authenticated:
        in_watchlist = Wishlist.query.filter_by(user_id=current_user.user_id, anime_id=id).first() is not None
        in_watched = Watched.query.filter_by(user_id=current_user.user_id, anime_id=id).first() is not None

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

    return render_template(
        'anime_detail.html',
        anime=anime,
        in_watchlist=in_watchlist,
        in_watched=in_watched,
        recommended=recommended
    )

@app.route('/anime/<int:id>/toggle-list', methods=['POST'])
@login_required
def toggle_list(id):
    list_type = request.form.get('list_type')
    Anime.query.get_or_404(id)

    model = Wishlist if list_type == 'watchlist' else Watched if list_type == 'watched' else None
    other_model = Watched if list_type == 'watchlist' else Wishlist if list_type == 'watched' else None

    if model is None:
        return redirect(url_for('anime_detail', id=id))

    existing = model.query.filter_by(user_id=current_user.user_id, anime_id=id).first()

    if existing:
        db.session.delete(existing)
    else:
        other_existing = other_model.query.filter_by(user_id=current_user.user_id, anime_id=id).first()
        if other_existing:
            db.session.delete(other_existing)

        db.session.add(model(user_id=current_user.user_id, anime_id=id))

    db.session.commit()
    return redirect(url_for('anime_detail', id=id))


@app.route('/watchlist')
@login_required
def watchlist():
    entries = Wishlist.query.filter_by(user_id=current_user.user_id).all()
    anime_ids = [e.anime_id for e in entries]
    anime_list = Anime.query.filter(Anime.id.in_(anime_ids)).all() if anime_ids else []
    return render_template('watchlist.html', anime_list=anime_list)


@app.route('/watched')
@login_required
def watched():
    entries = Watched.query.filter_by(user_id=current_user.user_id).all()
    anime_ids = [e.anime_id for e in entries]
    anime_list = Anime.query.filter(Anime.id.in_(anime_ids)).all() if anime_ids else []
    return render_template('watched.html', anime_list=anime_list)

@app.route('/about')
def about():
    return render_template('about.html')


if __name__ == "__main__":
    app.run(debug = True)