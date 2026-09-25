import pytest
from test_security import app, client  # reuse isolated fixtures


@pytest.fixture
def auth(app,client):
    from werkzeug.security import generate_password_hash
    app.config['EDITOR_PASSWORD_HASH']=generate_password_hash('TestEditorPassword123')
    response=client.post('/api/editor/login',json={'password':'TestEditorPassword123'})
    assert response.status_code==200
    return {'X-CSRF-Token':response.json['csrf']}


def test_content_auth(client):
    assert client.get('/api/editor/content').status_code == 401
    assert client.patch('/api/editor/content/hero',json={}).status_code == 401
    assert client.post('/api/editor/content/hero/publish',json={}).status_code == 401


def test_draft_publish_conflicts(client,auth):
    before=client.get('/api/content').json['content']['hero_line1']
    doc=client.get('/api/editor/content',headers=auth).json['sections']['hero']
    values=dict(doc['draft'],hero_line1='Your next adventure.')
    saved=client.patch('/api/editor/content/hero',headers=auth,json={'values':values,'revision':doc['revision']})
    assert saved.status_code==200
    assert client.get('/api/content').json['content']['hero_line1']==before
    assert client.patch('/api/editor/content/hero',headers=auth,json={'values':values,'revision':doc['revision']}).status_code==409
    assert client.post('/api/editor/content/hero/publish',headers=auth,json={'revision':doc['revision']}).status_code==409
    assert client.post('/api/editor/content/hero/publish',headers=auth,json={'revision':saved.json['section']['revision']}).status_code==200
    assert client.get('/api/content').json['content']['hero_line1']=='Your next adventure.'
    assert 'draft' not in client.get('/api/content').json


@pytest.mark.parametrize('image',['javascript:alert(1)','data:image/svg+xml,test','//evil.test/x','https://evil.test/x','https://images.unsplash.com.evil.test/photo-x','/assets/../../admin.html','https://user@images.unsplash.com/photo-x','https://images.unsplash.com:8443/photo-x'])
def test_unsafe_images(client,auth,image):
    doc=client.get('/api/editor/content',headers=auth).json['sections']['hero']
    doc['draft']['hero_image']=image
    assert client.patch('/api/editor/content/hero',headers=auth,json={'values':doc['draft'],'revision':doc['revision']}).status_code==400


def test_schema_and_plain_text(client,auth):
    doc=client.get('/api/editor/content',headers=auth).json['sections']['hero']
    for change in [{'hero_line1':[]},{'hero_line1':'x'*181},{'hero_line1':''},{'unexpected':'value'}]:
        assert client.patch('/api/editor/content/hero',headers=auth,json={'values':dict(doc['draft'],**change),'revision':doc['revision']}).status_code==400
    doc['draft']['hero_line1']='<img src=x onerror=alert(1)>'
    doc['draft']['hero_image']='https://images.unsplash.com/photo-1537373328362-c4dc43a03e41?w=900'
    assert client.patch('/api/editor/content/hero',headers=auth,json={'values':doc['draft'],'revision':doc['revision']}).status_code==200
    assert client.patch('/api/editor/content/missing',headers=auth,json={}).status_code==404
    assert client.post('/api/editor/content/hero/publish',headers=auth,json={'revision':True}).status_code==400
