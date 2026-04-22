"""
RESTful API for Process Triage application.
Provides read-only access to assessments for authenticated users.
All endpoints require valid session authentication.
"""

from flask import Blueprint, jsonify, session, request
from app import load_data

# Create API blueprint under /api/v1 prefix
api_bp = Blueprint('api', __name__, url_prefix='/api/v1')


def _get_current_user() -> dict | None:
    """Retrieve current authenticated user from session."""
    return session.get('user', None)


def _is_soft_deleted(record: dict) -> bool:
    """Check if a record is soft-deleted."""
    return bool(str(record.get('deleted_at', '')).strip())


def _record_to_json(record: dict) -> dict:
    """Convert internal record format to JSON API response."""
    user_payload = record.get('user', {})
    if not isinstance(user_payload, dict):
        user_payload = {}

    return {
        'id': record.get('id', ''),
        'name': record.get('name', ''),
        'description': record.get('purpose', ''),
        'process_type': record.get('type', ''),
        'path': record.get('path', ''),
        'status': record.get('status', 'partial'),
        'deep_dive_complete': bool(record.get('deep_dive_complete', False)),
        'created': record.get('created', ''),
        'updated': record.get('updated', ''),
        'owner_email': user_payload.get('email', ''),
    }


@api_bp.route('/assessments', methods=['GET'])
def list_assessments():
    """
    Get all non-deleted assessments for the authenticated user.
    
    Response:
        200: List of assessments in JSON format
        401: User not authenticated
    """
    user = _get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401

    user_email = str(user.get('email', '')).strip().lower()
    if not user_email:
        return jsonify({'error': 'User email not found in session'}), 401

    all_records = load_data()
    user_assessments = [
        r for r in all_records
        if str(r.get('user', {}).get('email', '')).strip().lower() == user_email
        and not _is_soft_deleted(r)
    ]

    return jsonify({
        'success': True,
        'count': len(user_assessments),
        'data': [_record_to_json(r) for r in user_assessments],
    }), 200


@api_bp.route('/assessments/<rec_id>', methods=['GET'])
def get_assessment(rec_id):
    """
    Get a specific assessment by ID.
    
    Args:
        rec_id: Assessment record ID (e.g., 'S001')
    
    Response:
        200: Single assessment in JSON format
        401: User not authenticated
        403: User does not own this assessment
        404: Assessment not found or is deleted
    """
    user = _get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401

    user_email = str(user.get('email', '')).strip().lower()
    if not user_email:
        return jsonify({'error': 'User email not found in session'}), 401

    rec_id_norm = str(rec_id).strip()

    all_records = load_data()
    record = next(
        (r for r in all_records if str(r.get('id', '')).strip() == rec_id_norm),
        None,
    )

    if not record:
        return jsonify({'error': 'Assessment not found'}), 404

    if _is_soft_deleted(record):
        return jsonify({'error': 'Assessment not found'}), 404

    record_email = str(record.get('user', {}).get('email', '')).strip().lower()
    if record_email != user_email:
        return jsonify({'error': 'Forbidden: You do not own this assessment'}), 403

    return jsonify({
        'success': True,
        'data': _record_to_json(record),
    }), 200


@api_bp.errorhandler(404)
def api_not_found(error):
    """Handle 404 errors for API routes."""
    return jsonify({'error': 'API endpoint not found'}), 404


@api_bp.errorhandler(405)
def api_method_not_allowed(error):
    """Handle 405 errors for API routes."""
    return jsonify({'error': 'Method not allowed'}), 405
