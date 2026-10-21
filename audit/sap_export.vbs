Option Explicit

Dim fromDate, toDate
Dim SapGuiAuto, application, connection, session, meanSession, grid, meanGrid
Dim rowIndex, meanRowIndex, material, gridValue, stockCategory, categoryValue, userName, quantity, ean
Dim triples, tripleKey, sessionCount, waitIndex, originalClipboard, clipboardSaved

If WScript.Arguments.Count <> 2 Then
    WScript.StdErr.WriteLine "Se requieren fecha inicial y fecha final."
    WScript.Quit 1
End If

fromDate = WScript.Arguments(0)
toDate = WScript.Arguments(1)
clipboardSaved = False

On Error Resume Next
Set SapGuiAuto = GetObject("SAPGUI")
If Err.Number <> 0 Then Fail "Abre SAP Logon e inicia sesión antes de comenzar la auditoría."

Set application = SapGuiAuto.GetScriptingEngine
If Err.Number <> 0 Then Fail "SAP GUI Scripting no está habilitado."

Set connection = application.Children(0)
If Err.Number <> 0 Then Fail "No se encontró una conexión abierta en SAP."

Set session = connection.Children(0)
If Err.Number <> 0 Then Fail "No se encontró una sesión abierta en SAP."
On Error GoTo 0

session.findById("wnd[0]").maximize
session.findById("wnd[0]/tbar[0]/okcd").Text = "/nZE16"
session.findById("wnd[0]").sendVKey 0
session.findById("wnd[0]/usr/ctxtDATABROWSE-TABLENAME").Text = "LTAP"
session.findById("wnd[0]").sendVKey 0
session.findById("wnd[0]/usr/ctxtI1-LOW").Text = "M02"
session.findById("wnd[0]/usr/txtI17-LOW").Text = "DAMAGE-BOX"
session.findById("wnd[0]/usr/ctxtI9-LOW").Text = FormatSapDate(fromDate)
session.findById("wnd[0]/usr/ctxtI9-HIGH").Text = FormatSapDate(toDate)
session.findById("wnd[0]").sendVKey 8

On Error Resume Next
Set grid = session.findById("wnd[0]/usr/cntlGRID1/shellcont/shell")
If Err.Number <> 0 Then
    Err.Clear
    Fail "No se encontró la tabla de resultados. Revisa los mensajes mostrados por SAP."
End If
On Error GoTo 0

Set triples = CreateObject("Scripting.Dictionary")
For rowIndex = 0 To grid.RowCount - 1
    EnsureGridRowVisible grid, rowIndex
    material = CleanValue(grid.GetCellValue(rowIndex, "MATNR"))
    gridValue = CleanValue(grid.GetCellValue(rowIndex, "J_3ASIZE"))
    stockCategory = CleanValue(grid.GetCellValue(rowIndex, "J_4KSCAT"))
    userName = CleanValue(grid.GetCellValue(rowIndex, "QNAME"))
    quantity = CleanValue(grid.GetCellValue(rowIndex, "NISTM"))
    If material <> "" And gridValue <> "" Then
        WScript.Echo "LTAP" & vbTab & material & vbTab & gridValue & vbTab & stockCategory & vbTab & userName & vbTab & quantity

        categoryValue = CategoryCode(stockCategory)
        tripleKey = material & Chr(30) & gridValue & Chr(30) & categoryValue
        If categoryValue <> "" And Not triples.Exists(tripleKey) Then triples.Add tripleKey, Array(material, gridValue, categoryValue)
    End If
Next

' Reuse the second SAP session for MEAN, as in the supplied SAP GUI recording.
If connection.Children.Count < 2 Then
    sessionCount = connection.Children.Count
    session.CreateSession
    For waitIndex = 1 To 100
        WScript.Sleep 100
        If connection.Children.Count > sessionCount Then Exit For
    Next
End If

If connection.Children.Count > 1 Then
    Set meanSession = connection.Children(1)
Else
    Set meanSession = session
End If

If triples.Count > 0 Then
    originalClipboard = GetClipboardText()
    clipboardSaved = True
    ReadMean meanSession, triples
    Call PutClipboardText(originalClipboard)
    clipboardSaved = False
End If

Sub ReadMean(activeSession, selectedTriples)
    Dim batchMaterials, batchGrids, batchCategories, tripleKey, tripleValues, selectionValues
    Dim resultMaterial, resultGridValue, resultCategory

    Set batchMaterials = CreateObject("Scripting.Dictionary")
    Set batchGrids = CreateObject("Scripting.Dictionary")
    Set batchCategories = CreateObject("Scripting.Dictionary")
    For Each tripleKey In selectedTriples.Keys
        tripleValues = selectedTriples(tripleKey)
        If Not batchMaterials.Exists(tripleValues(0)) Then batchMaterials.Add tripleValues(0), True
        If Not batchGrids.Exists(tripleValues(1)) Then batchGrids.Add tripleValues(1), True
        If Not batchCategories.Exists(tripleValues(2)) Then batchCategories.Add tripleValues(2), True
    Next

    activeSession.findById("wnd[0]/tbar[0]/okcd").Text = "/nZE16"
    activeSession.findById("wnd[0]").sendVKey 0
    activeSession.findById("wnd[0]/usr/ctxtDATABROWSE-TABLENAME").Text = "MEAN"
    activeSession.findById("wnd[0]").sendVKey 0
    activeSession.findById("wnd[0]/usr/txtMAX_SEL").Text = "5000"

    selectionValues = batchMaterials.Keys
    SetMultipleValues activeSession, "I1", selectionValues
    selectionValues = batchGrids.Keys
    SetMultipleValues activeSession, "I8", selectionValues
    selectionValues = batchCategories.Keys
    SetMultipleValues activeSession, "I9", selectionValues
    activeSession.findById("wnd[0]").sendVKey 8

    Set meanGrid = Nothing
    On Error Resume Next
    Set meanGrid = activeSession.findById("wnd[0]/usr/cntlGRID1/shellcont/shell")
    On Error GoTo 0
    If meanGrid Is Nothing Then Exit Sub

    ' Keep only Stock IDs before reading the result grid.
    meanGrid.SetCurrentCell -1, "EAN11"
    meanGrid.SelectColumn "EAN11"
    activeSession.findById("wnd[0]/tbar[1]/btn[29]").Press
    activeSession.findById("wnd[1]/usr/ssub%_SUBSCREEN_FREESEL:SAPLSSEL:1105/ctxt%%DYN001-LOW").Text = "*200*"
    activeSession.findById("wnd[1]").sendVKey 0

    Set meanGrid = activeSession.findById("wnd[0]/usr/cntlGRID1/shellcont/shell")
    If meanGrid.RowCount >= 5000 Then Fail "MEAN alcanzó el límite de 5000 resultados; reduce el rango de fechas."

    For meanRowIndex = 0 To meanGrid.RowCount - 1
        EnsureGridRowVisible meanGrid, meanRowIndex
        resultMaterial = CleanValue(meanGrid.GetCellValue(meanRowIndex, "MATNR"))
        resultGridValue = CleanValue(meanGrid.GetCellValue(meanRowIndex, "J_3AKORDX"))
        resultCategory = CleanValue(meanGrid.GetCellValue(meanRowIndex, "/AFS/EANBLOCK"))
        ean = CleanValue(meanGrid.GetCellValue(meanRowIndex, "EAN11"))
        If resultMaterial <> "" And resultGridValue <> "" And resultCategory <> "" And (Left(ean, 3) = "200" Or Left(ean, 5) = "00200") Then
            WScript.Echo "MEAN" & vbTab & resultMaterial & vbTab & resultGridValue & vbTab & resultCategory & vbTab & ean
        End If
    Next
End Sub

Sub EnsureGridRowVisible(tableGrid, targetRow)
    Dim firstRow, visibleRows

    firstRow = tableGrid.FirstVisibleRow
    visibleRows = tableGrid.VisibleRowCount
    If visibleRows < 1 Then visibleRows = 1

    If targetRow < firstRow Or targetRow >= firstRow + visibleRows Then
        tableGrid.FirstVisibleRow = targetRow
        WScript.Sleep 50
    End If
End Sub

Sub SetMultipleValues(activeSession, fieldName, selectedValues)
    If Not PutClipboardText(Join(selectedValues, vbCrLf)) Then Fail "No se pudo preparar el portapapeles para SAP."
    activeSession.findById("wnd[0]/usr/btn%_" & fieldName & "_%_APP_%-VALU_PUSH").Press
    activeSession.findById("wnd[1]/tbar[0]/btn[24]").Press
    activeSession.findById("wnd[1]/tbar[0]/btn[8]").Press
End Sub

Function GetClipboardText()
    Dim shell, process
    Set shell = CreateObject("WScript.Shell")
    Set process = shell.Exec("powershell.exe -NoProfile -STA -Command ""Add-Type -AssemblyName System.Windows.Forms; [Console]::Out.Write([Windows.Forms.Clipboard]::GetText())""")
    Do While process.Status = 0
        WScript.Sleep 10
    Loop
    GetClipboardText = process.StdOut.ReadAll()
End Function

Function PutClipboardText(value)
    Dim shell, process
    Set shell = CreateObject("WScript.Shell")
    Set process = shell.Exec("powershell.exe -NoProfile -STA -Command ""Add-Type -AssemblyName System.Windows.Forms; $value = [Console]::In.ReadToEnd(); if ($value.Length -eq 0) { [Windows.Forms.Clipboard]::Clear() } else { [Windows.Forms.Clipboard]::SetText($value) }""")
    process.StdIn.Write value
    process.StdIn.Close
    Do While process.Status = 0
        WScript.Sleep 10
    Loop
    PutClipboardText = (process.ExitCode = 0)
End Function

Function CleanValue(value)
    CleanValue = Trim(Replace(Replace(CStr(value), vbTab, " "), vbCrLf, " "))
End Function

Function CategoryCode(value)
    Dim character, index

    CategoryCode = ""
    For index = 1 To Len(value)
        character = UCase(Mid(value, index, 1))
        If Asc(character) < 65 Or Asc(character) > 90 Then Exit For
        CategoryCode = CategoryCode & character
    Next
End Function

Function FormatSapDate(value)
    FormatSapDate = Right(value, 2) & "." & Mid(value, 5, 2) & "." & Left(value, 4)
End Function

Sub Fail(message)
    If clipboardSaved Then
        On Error Resume Next
        Call PutClipboardText(originalClipboard)
        On Error GoTo 0
    End If
    WScript.StdErr.WriteLine message
    WScript.Quit 1
End Sub
