from django import forms

class ProjectOverviewForm(forms.Form):
    project_code = forms.CharField(label="Project Code", required=False)
    checksum_value = forms.CharField(label="Checksum Value", required=False)
    test_engineer = forms.CharField(label="Test Engineer", required=False)
    app_sw_version = forms.CharField(label="Application SW Version", required=False)
    developer = forms.CharField(label="Developer", required=False)
    project_stage = forms.CharField(label="Project Stage", required=False)
    sw_part_number = forms.CharField(label="Software Part Number", required=False)
    vcu_platform = forms.CharField(label="VCU Platform", required=False)
    hw_part_number = forms.CharField(label="Hardware Part Number", required=False)
    bootloader_sw_version = forms.CharField(label="Bootloader SW Version", required=False)
    dbc_detail = forms.CharField(label="DBC Detail", required=False, widget=forms.Textarea(attrs={'rows':3}))
