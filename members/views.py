import json

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts_core.audit import audit
from accounts_core.rbac import can, perm_required
from core.models import next_ref
from core.ui import C, M, Table, paginate

from .forms import DocumentForm, MemberForm
from .models import Member, MemberDocument


@perm_required("members.view")
def member_list(request):
    q = request.GET.get("q", "").strip()
    st = request.GET.get("status", "")
    qs = Member.objects.all()
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(member_no__icontains=q) | Q(phone__icontains=q))
    if st:
        qs = qs.filter(status=st)
    page, qstr = paginate(request, qs)
    from core.templatetags.sacco import mask_phone
    t = Table(["Member no.", "Name", "Phone", "Joined", "Status", ""])
    for m in page:
        t.add([C(m.member_no, f"/members/{m.pk}/"), m.full_name, mask_phone(m.phone), f"{m.joined_on:%d %b %Y}", C(m.get_status_display(), badge=True),
               C("Open", f"/members/{m.pk}/")])
    actions = [("Register member", "/members/new/", "primary")] if can(request.user, "members.manage") else []
    return render(request, "generic_list.html", {
        "title": "Members", "subtitle": "Register and manage SACCO members.", "table": t, "page": page, "qs": qstr, "q": q,
        "search": "Search name, member no. or phone", "actions": actions,
        "filters": [("status", "Status", [("", "All")] + list(Member.STATUS), st)]})


@perm_required("members.manage")
def member_create(request):
    form = MemberForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            m = form.save(commit=False)
            m.member_no = form.cleaned_data["member_no"] or next_ref("MB", "member_no")
            while Member.objects.filter(member_no=m.member_no).exists():
                m.member_no = next_ref("MB", "member_no")
            m.set_nin(form.cleaned_data["nin"])
            m.consent_given, m.consent_at, m.created_by = True, timezone.now(), request.user
            m.save()
            audit("member.register", "member", "Member", m.pk, m.member_no, new={"name": m.full_name, "consent": True},
                  detail="Member registered with data-protection consent")
        messages.success(request, f"Member {m.member_no} registered. Next, open a savings account.")
        return redirect("members:detail", pk=m.pk)
    return render(request, "generic_form.html", {"title": "Register new member", "form": form, "submit": "Register member", "multipart": True,
                                                 "cancel": "/members/", "intro": "Fields marked * are required. Consent must be confirmed before saving."})


@perm_required("members.view")
def member_detail(request, pk):
    m = get_object_or_404(Member, pk=pk)
    from loans.models import Loan
    accounts = m.accounts.all()
    loans = m.loans.all()[:20]
    guarantees = m.guarantees.select_related("loan").filter(status="active")
    return render(request, "members/detail.html", {"m": m, "accounts": accounts, "loans": loans, "docs": m.documents.all(), "guarantees": guarantees})


@perm_required("members.manage")
def member_edit(request, pk):
    m = get_object_or_404(Member, pk=pk)
    form = MemberForm(request.POST or None, request.FILES or None, instance=m)
    if request.method == "POST" and form.is_valid():
        old = {"name": m.full_name, "phone": "****" + m.phone[-3:]}
        obj = form.save(commit=False)
        obj.set_nin(form.cleaned_data["nin"])
        obj.save()
        audit("member.update", "member", "Member", m.pk, m.member_no, old=old, new={"name": obj.full_name})
        messages.success(request, "Member details updated.")
        return redirect("members:detail", pk=m.pk)
    return render(request, "generic_form.html", {"title": f"Edit {m.full_name}", "form": form, "submit": "Save changes", "multipart": True, "cancel": f"/members/{m.pk}/"})


@require_POST
@perm_required("members.manage")
def member_status(request, pk):
    m = get_object_or_404(Member, pk=pk)
    new = request.POST.get("status")
    if new not in dict(Member.STATUS):
        raise Http404
    if new in ("exited", "suspended") and m.loans.filter(status__in=("active", "approved")).exists():
        messages.error(request, "This member has active loans. Clear or settle them first.")
        return redirect("members:detail", pk=pk)
    old = m.status
    m.status = new
    m.save(update_fields=["status"])
    audit("member.status", "member", "Member", m.pk, m.member_no, old={"status": old}, new={"status": new})
    messages.success(request, f"Member marked {m.get_status_display().lower()}.")
    return redirect("members:detail", pk=pk)


@perm_required("members.manage")
def document_upload(request, pk):
    m = get_object_or_404(Member, pk=pk)
    form = DocumentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        d = form.save(commit=False)
        d.member, d.uploaded_by = m, request.user
        d.save()
        audit("member.document", "member", "Member", m.pk, m.member_no, new={"kind": d.kind, "doc": d.pk}, detail="Document uploaded")
        messages.success(request, "Document stored securely.")
        return redirect("members:detail", pk=pk)
    return render(request, "generic_form.html", {"title": f"Upload document - {m.full_name}", "form": form, "submit": "Upload", "multipart": True,
                                                 "intro": "PDF, JPG or PNG up to 5 MB. Files are stored privately and only served to authorised staff.", "cancel": f"/members/{pk}/"})


@perm_required("members.view")
def document_download(request, pk):
    d = get_object_or_404(MemberDocument, pk=pk)
    audit("member.document_view", "member", "MemberDocument", d.pk, d.member.member_no, detail=f"{d.kind} viewed")
    resp = FileResponse(d.file.open("rb"))
    resp["Content-Disposition"] = f'inline; filename="{d.member.member_no}-{d.kind}-{d.pk}.{d.file.name.rsplit(".", 1)[-1]}"'
    return resp


@perm_required("members.view")
def member_photo(request, pk):
    m = get_object_or_404(Member, pk=pk)
    if not m.photo:
        raise Http404
    return FileResponse(m.photo.open("rb"))


@perm_required("members.view")
def member_statement(request, pk):
    m = get_object_or_404(Member, pk=pk)
    from savings.models import SavingsTransaction
    txs = SavingsTransaction.objects.filter(account__member=m).select_related("account").order_by("-date", "-id")[:200]
    return render(request, "members/statement.html", {"m": m, "txs": txs, "accounts": m.accounts.all(), "loans": m.loans.exclude(status="rejected")})


@perm_required("members.export")
def member_export(request, pk):
    """Data-subject access request: complete export of the personal data held about a member."""
    m = get_object_or_404(Member, pk=pk)
    data = {
        "member": {"member_no": m.member_no, "name": m.full_name, "gender": m.gender, "date_of_birth": str(m.date_of_birth or ""),
                   "national_id": m.nin, "phone": m.phone, "alt_phone": m.alt_phone, "email": m.email, "address": m.address,
                   "occupation": m.occupation, "next_of_kin": {"name": m.next_of_kin_name, "relation": m.next_of_kin_relation, "phone": m.next_of_kin_phone},
                   "status": m.status, "joined_on": str(m.joined_on), "consent_given": m.consent_given, "consent_at": str(m.consent_at or "")},
        "accounts": [{"account_no": a.account_no, "kind": a.kind, "balance": str(a.balance), "status": a.status} for a in m.accounts.all()],
        "transactions": [{"ref": t.ref, "date": str(t.date), "kind": t.kind, "amount": str(t.amount), "balance_after": str(t.balance_after)}
                         for a in m.accounts.all() for t in a.transactions.all()],
        "loans": [{"loan_no": l.loan_no, "principal": str(l.principal), "rate": str(l.interest_rate), "method": l.interest_method, "status": l.status,
                   "applied_on": str(l.applied_on)} for l in m.loans.all()],
        "documents": [{"kind": d.kind, "uploaded_at": str(d.uploaded_at)} for d in m.documents.all()],
        "generated_at": timezone.now().isoformat(),
    }
    audit("member.data_export", "member", "Member", m.pk, m.member_no, detail="Personal data export generated (data subject request)")
    resp = HttpResponse(json.dumps(data, indent=2), content_type="application/json")
    resp["Content-Disposition"] = f'attachment; filename="{m.member_no}-personal-data.json"'
    return resp
