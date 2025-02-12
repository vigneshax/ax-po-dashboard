import decimal
import traceback
import streamlit as st
import pandas as pd
import plotly.express as px
import boto3

class FlaggedOrdersDashboard:
    def __init__(self):
        st.set_page_config(layout="wide")
        if 'df' not in st.session_state:
            st.session_state.df = None
        if 'flagged_orders' not in st.session_state:
            st.session_state.flagged_orders = None
        if 'flagged_fields_count' not in st.session_state:
            st.session_state.flagged_fields_count = None
        self.dynamodb = boto3.resource('dynamodb')
        self.table_name = "afx-kg-hist-transactions"
        self.table = self.dynamodb.Table(self.table_name)
        self.run()
    
    def load_data(self):
        if st.session_state.df is None:
            response = self.table.scan()
            items = response.get('Items', [])
            st.session_state.df = pd.DataFrame(items)
        return st.session_state.df
    
    def analyze_flagged_orders(self):
        self.df = self.load_data()
        st.session_state.flagged_orders = self.df[(self.df['IsFlagged'] == 'Y') & (self.df['ReviewStatus'] == 'pending-review')]
        self.df['FlaggedFields'] = self.df['FlaggedFields'].fillna("").astype(str)
        flagged_fields_series = (
            self.df['FlaggedFields']
            .str.replace('"', '')
            .str.split(', ')
            .explode()
            .str.strip()
        )
        
        def normalize_field(field):
            parts = field.split('.')
            if len(parts) > 1 and parts[-1] in {'partId', 'decorationId', 'locationId', 'Imprint Color'}:
                return f"Line Items.{parts[-1]}"
            return field
        
        flagged_fields_series = flagged_fields_series.apply(normalize_field)
        
        remove_fields = {
            '',
            'Shipping Address.Ship To',
            'PO Contact.PO Contact Person',
            'PO Contact.PO Contact Email',
            'Shipping Address.City',
        }
        flagged_fields_series = flagged_fields_series[~flagged_fields_series.isin(remove_fields)]
        
        st.session_state.flagged_fields_count = flagged_fields_series.value_counts()
    
    def calculate_order_stats(self, df):
        total_orders = df['PackageID'].nunique()
        total_flagged = df[df['IsFlagged'] == 'Y']['PackageID'].nunique()
        total_unflagged = total_orders - total_flagged
        flagging_percentage = (total_flagged / total_orders * 100) if total_orders > 0 else 0
        return total_orders, total_flagged, total_unflagged, flagging_percentage
    
    def extract_nested_value(self, data, key_path):
        """
        Extracts a nested value from a dictionary given a dot-separated key path.
        Example: extract_nested_value(ocr_dict, "Line Items.0.locationId")
        """
        k1, idx, k2 = key_path.split(".")

        idx = int(idx)
        if k2 == 'locationId':
            k2 = "locationName"
        elif k2 == 'partId':
            k2 = "partDescription"
        elif k2 == 'decorationId':
            k2 = "decorationName"

        try:
            out = data[k1][idx][k2]
            # print(f"Extracted: {key_path} -> {out}")
            return out if out else ""  
        except (KeyError, IndexError, TypeError) as e:
            # print(f"Error extracting '{key_path}': {e}")
            # print(f"Data: {data}")
            traceback.print_exc()
            return ""  

    # Function to transform OCRValues and Suggestions dynamically
    def transform_flagged_fields(self, row):
        flagged_fields = row["FlaggedFields"].split(", ")
        flagged_fields = [f for f in flagged_fields if "Line Items." in f]

        transformed_ocr = {}
        transformed_suggestions = {}

        for field in flagged_fields:
            if not "Line Items." in field:
                continue

            # print(f"Processing Field: {field}")
            ocr_value = self.extract_nested_value(row["OCRValues"], field)
            suggestions_value = self.extract_nested_value(row["Suggestions"], field)

            # print(f"OCRValue for {field}: {ocr_value}")
            # print(f"Suggestions for {field}: {suggestions_value}")

            # Store extracted values in a dictionary
            transformed_ocr[field] = ocr_value
            transformed_suggestions[field] = suggestions_value

        # Update row with transformed values
        row["Transformed_OCRValues"] = transformed_ocr
        row["Transformed_Suggestions"] = transformed_suggestions
        
        return row

    def render_dashboard(self):
        st.title("📊 Flagged Orders Dashboard")
        
        self.analyze_flagged_orders()
        self.df = st.session_state.df
        self.df['Date'] = pd.to_datetime(self.df['Date'], errors='coerce')
        self.df = self.df.dropna(subset=['Date'])
        
        st.subheader("Order Summary")
        min_date, max_date = self.df['Date'].min().date(), self.df['Date'].max().date()
        start_date, end_date = st.date_input("Select Date Range", value=[min_date, max_date], min_value=min_date, max_value=max_date)
        
        filtered_df = self.df[(self.df['Date'] >= pd.to_datetime(start_date)) & (self.df['Date'] <= pd.to_datetime(end_date))]
        total_orders, total_flagged, total_unflagged, flagging_percentage = self.calculate_order_stats(filtered_df)
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Orders", total_orders)
        col2.metric("Total Flagged Orders", total_flagged)
        col3.metric("Total AllOk Orders", total_unflagged)
        col4.metric("Flagging Percentage", f"{flagging_percentage:.2f}%")

        st.divider()

        st.subheader("Flagged Orders by Account")
        self.df['AccountNumber'] = self.df['AccountNumber'].apply(lambda x: int(x) if isinstance(x, (decimal.Decimal, str)) and str(x).isdigit() else x)
        unique_accounts = self.df[(self.df['IsFlagged'] == 'Y') & (self.df['ReviewStatus'] == 'pending-review')]['AccountNumber'].unique()
        print(unique_accounts)
        for account in unique_accounts:
            with st.expander(f"Account: {account}"):
                account_df = self.df[self.df['AccountNumber'] == account]
                # print(account_df)
                total_orders, total_flagged, total_unflagged, flagging_percentage = self.calculate_order_stats(account_df)
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total Orders", total_orders)
                col2.metric("Total Flagged Orders", total_flagged)
                col3.metric("Total AllOk Orders", total_unflagged)
                col4.metric("Flagging Percentage", f"{flagging_percentage:.2f}%")
        
        st.divider()

        
        st.subheader("Most Flagged Fields")
        st.bar_chart(st.session_state.flagged_fields_count)
        
        st.subheader("Flagged Orders Over Time")
        flagged_over_time = st.session_state.flagged_orders.groupby(self.df['Date'].dt.date).size()
        st.line_chart(flagged_over_time)

        st.subheader("Order Status Distribution")
        fig = px.pie(self.df, names='ReviewStatus', title='Review Status Distribution')
        st.plotly_chart(fig)

        st.subheader("Line Items - Flagged Orders")
        filtered_data= st.session_state.df[
            (st.session_state.df["Message"] == "Empty Configuration found.") &
            (st.session_state.df["ReviewStatus"] == "pending-approval")
        ]

        filtered_data = filtered_data.apply(self.transform_flagged_fields, axis=1)
        st.write(filtered_data[["PackageID", "AccountNumber", "ProductIDs", "Date","Transformed_OCRValues", "Transformed_Suggestions"]])
       

        st.subheader("Dataset Overview")
        st.write(self.df.head(50))
        
        st.info("Data is being fetched directly from DynamoDB and stored in session state.")
    
    def run(self):
        self.render_dashboard()
        
if __name__ == "__main__":
    FlaggedOrdersDashboard()
