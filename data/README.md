# Policy documents

Put PDFs here, one sub-folder per policy area. The folder name becomes the category shown in the app
and used for filtering:

```
data/
├── employee_policies/      -> "Employee Policies"
├── hr_policies/            -> "HR Policies"
├── finance_policies/       -> "Finance Policies"
├── vendor_policies/        -> "Vendor Policies"
├── client_policies/        -> "Client Policies"
├── sub_contractor_policies/-> "Sub Contractor Policies"
└── infrastructure_policies/-> "Infrastructure Policies"
```

Everything in this folder except this README is ignored by git, because policy documents are often
confidential. Then run `python ingest.py` to load them into the index.
