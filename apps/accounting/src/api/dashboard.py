from flask import (
    Blueprint, render_template, redirect, url_for, g
)
from src.api.auth import login_required

bp = Blueprint('dashboard', __name__)

@bp.route('/')
@login_required
def index():
    """Redirect to appropriate dashboard based on user role."""
    if g.user and g.user['role'] == 'manager':
        return redirect(url_for('manager.index'))
    else:
        return redirect(url_for('reception.index'))
