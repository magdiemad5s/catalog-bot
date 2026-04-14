import os
import glob

templates = glob.glob('templates/*.html')
for t in templates:
    with open(t, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if '{% block extra_style %}' in content and t not in ['templates\\base_admin.html', 'templates\\base_public.html']:
        new_content = content.replace('{% block extra_style %}', '{% block extra_style %}\n<style>')
        
        # We need to find the {% endblock %} right after {% block extra_style %}
        # and replace it with </style>\n{% endblock %}
        # This regex replaces the FIRST combination, but we can do it with string split:
        parts = new_content.split('{% block extra_style %}\n<style>')
        if len(parts) > 1:
            after_block = parts[1]
            # Replace the first {% endblock %} in the after_block
            after_block = after_block.replace('{% endblock %}', '</style>\n{% endblock %}', 1)
            final_content = parts[0] + '{% block extra_style %}\n<style>' + after_block
            
            with open(t, 'w', encoding='utf-8') as f:
                f.write(final_content)
                print(f"Updated {t}")
